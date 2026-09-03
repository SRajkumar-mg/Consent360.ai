"""Seed script - populates roles, users, reference data, purposes, policies,
customers, consents and audit history so the platform looks functional immediately."""

import random
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.core.rbac import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from app.core.security import hash_password
from app.core.config import get_settings
from app.core.encryption import hmac_digest
from app.models.entities import (
    Consent,
    ConsentEvidence,
    ConsentHistory,
    CrmCustomer,
    Customer,
    DataCategory,
    Policy,
    PolicyVersion,
    ProcessingActivity,
    Purpose,
    PurposeVersion,
    Role,
    User,
)
from app.services import consent as consent_service
from app.services.decision_engine import get_active_policy

settings = get_settings()


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def seed(db: Session) -> None:
    # ------------------------------------------------------------------ roles
    for name, perms in ROLE_PERMISSIONS.items():
        existing = db.query(Role).filter(Role.name == name).first()
        if existing:
            if set(existing.permissions) != set(perms):
                existing.permissions = perms
        else:
            db.add(Role(name=name, description=ROLE_DESCRIPTIONS.get(name, ""), permissions=perms, is_system=True))
    db.commit()

    # Remap legacy roles onto the current roles, then drop the old ones.
    # Note: "consent_manager" and "auditor" are now first-class roles (see
    # app.core.rbac) rather than legacy names, so they are intentionally
    # excluded from this remap table.
    legacy_role_map = {
        "consent_admin": "admin",
        "privacy_officer": "admin",
        "data_steward": "admin",
        "customer_service": "consent_manager",
        "read_only": "viewer",
        "query_analyst": "viewer",
    }
    for legacy_name, new_name in legacy_role_map.items():
        legacy = db.query(Role).filter(Role.name == legacy_name).first()
        if not legacy:
            continue
        new_role = db.query(Role).filter(Role.name == new_name).first()
        if new_role:
            db.query(User).filter(User.role_id == legacy.id).update(
                {User.role_id: new_role.id}, synchronize_session=False
            )
        db.delete(legacy)
    db.commit()

    # ------------------------------------------------------------------ users
    if db.query(User).count() == 0:
        admin_role = db.query(Role).filter(Role.name == "admin").first()
        viewer_role = db.query(Role).filter(Role.name == "viewer").first()
        manager_role = db.query(Role).filter(Role.name == "consent_manager").first()
        auditor_role = db.query(Role).filter(Role.name == "auditor").first()
        if admin_role is None or viewer_role is None:
            raise RuntimeError("admin/viewer roles must exist before seeding users - re-run role seeding or create the roles table")
        manager_role = manager_role or viewer_role
        auditor_role = auditor_role or viewer_role

        users = [
            User(username=settings.SEED_ADMIN_USERNAME, full_name="System Administrator",
                 email="admin@consent.local", email_search=hmac_digest("admin@consent.local"),
                 password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                 role_id=admin_role.id, is_active=True),
            User(username="privacy.officer", full_name="Priya Nair", email="privacy@consent.local",
                 email_search=hmac_digest("privacy@consent.local"),
                 password_hash=hash_password("Privacy@1234"), role_id=admin_role.id, is_active=True),
            User(username="data.steward", full_name="Arjun Mehta", email="steward@consent.local",
                 email_search=hmac_digest("steward@consent.local"),
                 password_hash=hash_password("Steward@1234"), role_id=admin_role.id, is_active=True),
            User(username="customer.service", full_name="Kavya Sharma", email="service@consent.local",
                 email_search=hmac_digest("service@consent.local"),
                 password_hash=hash_password("Service@1234"), role_id=manager_role.id, is_active=True),
            User(username="auditor", full_name="Rahul Verma", email="auditor@consent.local",
                 email_search=hmac_digest("auditor@consent.local"),
                 password_hash=hash_password("Auditor@1234"), role_id=auditor_role.id, is_active=True),
            User(username="readonly", full_name="Inspect User", email="readonly@consent.local",
                 email_search=hmac_digest("readonly@consent.local"),
                 password_hash=hash_password("Readonly@1234"), role_id=viewer_role.id, is_active=True),
            User(username="viewer", full_name="View Only", email="viewer@consent.local",
                 email_search=hmac_digest("viewer@consent.local"),
                 password_hash=hash_password("Viewer@1234"), role_id=viewer_role.id, is_active=True),
        ]
        db.add_all(users)
        db.commit()

    # ------------------------------------------------------- reference data
    categories_by_code: dict[str, DataCategory] = {}
    if db.query(DataCategory).count() == 0:
        categories = [
            DataCategory(name="Name", code="name", description="Customer name"),
            DataCategory(name="Email", code="email", description="Email address"),
            DataCategory(name="Phone", code="phone", description="Phone number"),
            DataCategory(name="Address", code="address", description="Postal address"),
            DataCategory(name="Identity", code="identity", description="Identity information"),
            DataCategory(name="Financial", code="financial", description="Financial information"),
            DataCategory(name="Location", code="location", description="Location data"),
            DataCategory(name="Device", code="device", description="Device identifiers"),
            DataCategory(name="Behavioral", code="behavioral", description="Behavioral data"),
            DataCategory(name="Health", code="health", description="Health data"),
            DataCategory(name="Customer Profile", code="profile", description="Customer profile data"),
        ]
        db.add_all(categories)
        db.commit()
    for c in db.query(DataCategory).all():
        categories_by_code[c.code] = c

    activities_by_code: dict[str, ProcessingActivity] = {}
    if db.query(ProcessingActivity).count() == 0:
        activities = [
            ProcessingActivity(name="Send Marketing Email", code="send_marketing_email", description="Email marketing campaigns"),
            ProcessingActivity(name="Send SMS", code="send_sms", description="SMS notifications and campaigns"),
            ProcessingActivity(name="Personalize Offers", code="personalize_offers", description="Personalized product offers"),
            ProcessingActivity(name="Analytics Processing", code="analytics", description="Product and marketing analytics"),
            ProcessingActivity(name="Customer Support", code="customer_support", description="Customer support operations"),
            ProcessingActivity(name="Fraud Analysis", code="fraud_analysis", description="Fraud detection and prevention"),
            ProcessingActivity(name="Third-Party Sharing", code="third_party_sharing", description="Sharing with partners"),
            ProcessingActivity(name="Research Processing", code="research", description="Research and product development"),
            ProcessingActivity(name="Communication", code="communication", description="Transactional communications"),
        ]
        db.add_all(activities)
        db.commit()
    for a in db.query(ProcessingActivity).all():
        activities_by_code[a.code] = a

    # ----------------------------------------------------------------- purposes
    purpose_specs = [
        {
            "name": "Strictly necessary cookies", "code": "strictly_necessary",
            "legal_basis": "LEGITIMATE_INTEREST", "lawful_basis": "S7_F", "retention": 365, "requires_consent": False,
            "cats": ["identity", "device"], "acts": ["customer_support", "fraud_analysis", "communication"],
            "consent_text": "",
            "translations": {
                "ta": {"name": "கண்டிப்பாகத் தேவையான குக்கீகள்",
                       "description": "எப்போதும் செயலில். போர்ட்டல் வேலை செய்ய தேவை — உள்நுழைவு, பாதுகாப்பு மற்றும் சம்மதப் பதிவுகள்.", "consent_text": ""},
                "hi": {"name": "कड़ाई से आवश्यक कुकीज़",
                       "description": "हमेशा सक्रिय। पोर्टल के काम करने के लिए आवश्यक — लॉगिन, सुरक्षा और सहमति रिकॉर्ड।", "consent_text": ""},
                "kn": {"name": "ಕಟ್ಟುನಿಟ್ಟಾಗಿ ಅಗತ್ಯವಾದ ಕುಕೀಗಳು",
                       "description": "ಯಾವಾಗಲೂ ಸಕ್ರಿಯ. ಪೋರ್ಟಲ್ ಕಾರ್ಯನಿರ್ವಹಿಸಲು ಅಗತ್ಯ — ಲಾಗಿನ್, ಸುರಕ್ಷತೆ ಮತ್ತು ಸಮ್ಮತಿ ದಾಖಲೆಗಳು.", "consent_text": ""},
                "ml": {"name": "കർശനമായി ആവശ്യമായ കുക്കികൾ",
                       "description": "എപ്പോഴും സജീവം. പോർട്ടൽ പ്രവർത്തിക്കാൻ ആവശ്യമാണ് — ലോഗിൻ, സുരക്ഷ, സമ്മത രേഖകൾ.", "consent_text": ""},
                "te": {"name": "తప్పనిసరిగా అవసరమైన కుకీలు",
                       "description": "ఎల్లప్పుడూ చురుకుగా ఉంటాయి. పోర్టల్ పనిచేయడానికి అవసరం — లాగిన్, భద్రత మరియు సమ్మతి రికార్డులు.", "consent_text": ""},
            },
        },
        {
            "name": "Functional cookies", "code": "functional",
            "legal_basis": "CONSENT", "retention": 365, "requires_consent": True,
            "cats": ["profile", "device"], "acts": ["personalize_offers"],
            "consent_text": "I consent to functional cookies being used to remember my language and personal preferences.",
            "translations": {
                "ta": {"name": "செயல்பாட்டு குக்கீகள்",
                       "description": "உங்கள் மொழி மற்றும் தனிப்பட்ட விருப்பங்களை நினைவில் வைக்கும்.",
                       "consent_text": "எனது மொழி மற்றும் தனிப்பட்ட விருப்பங்களை நினைவில் வைக்க செயல்பாட்டு குக்கீகள் பயன்படுத்தப்படுவதற்கு நான் சம்மதிக்கிறேன்."},
                "hi": {"name": "कार्यात्मक कुकीज़",
                       "description": "आपकी भाषा और व्यक्तिगत प्राथमिकताओं को याद रखें।",
                       "consent_text": "मैं अपनी भाषा और व्यक्तिगत प्राथमिकताओं को याद रखने के लिए कार्यात्मक कुकीज़ के उपयोग की सहमति देता हूँ।"},
                "kn": {"name": "ಕಾರ್ಯನಿರ್ವಹಣಾ ಕುಕೀಗಳು",
                       "description": "ನಿಮ್ಮ ಭಾಷೆ ಮತ್ತು ವೈಯಕ್ತಿಕ ಆದ್ಯತೆಗಳನ್ನು ನೆನಪಿಡಿ.",
                       "consent_text": "ನನ್ನ ಭಾಷೆ ಮತ್ತು ವೈಯಕ್ತಿಕ ಆದ್ಯತೆಗಳನ್ನು ನೆನಪಿಡಲು ಕಾರ್ಯನಿರ್ವಹಣಾ ಕುಕೀಗಳನ್ನು ಬಳಸಲು ನಾನು ಸಮ್ಮತಿಸುತ್ತೇನೆ."},
                "ml": {"name": "ഫങ്ഷണൽ കുക്കികൾ",
                       "description": "നിങ്ങളുടെ ഭാഷയും വ്യക്തിഗത മുൻഗണനകളും ഓർമ്മിക്കുക.",
                       "consent_text": "എന്റെ ഭാഷയും വ്യക്തിഗത മുൻഗണനകളും ഓർമ്മിക്കുന്നതിന് ഫങ്ഷണൽ കുക്കികൾ ഉപയോഗിക്കുന്നതിന് ഞാൻ സമ്മതിക്കുന്നു."},
                "te": {"name": "ఫంక్షనల్ కుకీలు",
                       "description": "మీ భాష మరియు వ్యక్తిగత ప్రాధాన్యతలను గుర్తుంచుకోండి.",
                       "consent_text": "నా భాష మరియు వ్యక్తిగత ప్రాధాన్యతలను గుర్తుంచుకోవడానికి ఫంక్షనల్ కుకీలను ఉపయోగించడానికి నేను సమ్మతిస్తున్నాను."},
            },
        },
        {
            "name": "Performance & analytics cookies", "code": "analytics",
            "legal_basis": "CONSENT", "retention": 730, "requires_consent": True,
            "cats": ["behavioral", "device", "location"], "acts": ["analytics"],
            "consent_text": "I consent to my usage data being collected for performance and analytics purposes so the platform can be improved.",
            "translations": {
                "ta": {"name": "செயல்திறன் & பகுப்பாய்வு குக்கீகள்",
                       "description": "போர்ட்டலை மேம்படுத்த அது எவ்வாறு பயன்படுத்தப்படுகிறது என்பதைப் புரிந்துகொள்ள உதவுகிறது.",
                       "consent_text": "போர்ட்டலை மேம்படுத்த எனது பயன்பாட்டுத் தரவு செயல்திறன் மற்றும் பகுப்பாய்வு நோக்கங்களுக்காக சேகரிக்கப்படுவதற்கு நான் சம்மதிக்கிறேன்."},
                "hi": {"name": "प्रदर्शन और विश्लेषण कुकीज़",
                       "description": "यह समझने में मदद करें कि पोर्टल का उपयोग कैसे किया जाता है ताकि इसे बेहतर बनाया जा सके।",
                       "consent_text": "मैं पोर्टल को बेहतर बनाने के लिए अपने उपयोग डेटा को प्रदर्शन और विश्लेषण उद्देश्यों के लिए एकत्र करने की सहमति देता हूँ।"},
                "kn": {"name": "ಕಾರ್ಯಕ್ಷಮತೆ ಮತ್ತು ವಿಶ್ಲೇಷಣೆ ಕುಕೀಗಳು",
                       "description": "ಪೋರ್ಟಲ್ ಅನ್ನು ಸುಧಾರಿಸಲು ಅದನ್ನು ಹೇಗೆ ಬಳಸಲಾಗುತ್ತದೆ ಎಂಬುದನ್ನು ಅರ್ಥಮಾಡಿಕೊಳ್ಳಲು ಸಹಾಯ ಮಾಡುತ್ತದೆ.",
                       "consent_text": "ಪೋರ್ಟಲ್ ಅನ್ನು ಸುಧಾರಿಸಲು ನನ್ನ ಬಳಕೆಯ ಡೇಟಾವನ್ನು ಕಾರ್ಯಕ್ಷಮತೆ ಮತ್ತು ವಿಶ್ಲೇಷಣೆ ಉದ್ದೇಶಗಳಿಗಾಗಿ ಸಂಗ್ರಹಿಸಲು ನಾನು ಸಮ್ಮತಿಸುತ್ತೇನೆ."},
                "ml": {"name": "പെർഫോമൻസ് & അനലിറ്റിക്സ് കുക്കികൾ",
                       "description": "പോർട്ടൽ എങ്ങനെ ഉപയോഗിക്കുന്നു എന്ന് മനസ്സിലാക്കി അത് മെച്ചപ്പെടുത്താൻ സഹായിക്കുന്നു.",
                       "consent_text": "പോർട്ടൽ മെച്ചപ്പെടുത്തുന്നതിന് എന്റെ ഉപയോഗ ഡാറ്റ പെർഫോമൻസ്, അനലിറ്റിക്സ് ആവശ്യങ്ങൾക്കായി ശേഖരിക്കുന്നതിന് ഞാൻ സമ്മതിക്കുന്നു."},
                "te": {"name": "పనితీరు & విశ్లేషణ కుకీలు",
                       "description": "పోర్టల్ను మెరుగుపరచడానికి దానిని ఎలా ఉపయోగిస్తున్నారో అర్థం చేసుకోవడానికి సహాయపడుతుంది.",
                       "consent_text": "పోర్టల్ను మెరుగుపరచడానికి నా వినియోగ డేటాను పనితీరు & విశ్లేషణ ప్రయోజనాల కోసం సేకరించడానికి నేను సమ్మతిస్తున్నాను."},
            },
        },
        {
            "name": "Advertising & social media cookies", "code": "advertising",
            "legal_basis": "CONSENT", "retention": 730, "requires_consent": True,
            "cats": ["email", "phone", "behavioral", "profile"], "acts": ["send_marketing_email", "send_sms", "third_party_sharing", "personalize_offers"],
            "consent_text": "I consent to advertising and social media cookies being set so partners can show me relevant advertising.",
            "translations": {
                "ta": {"name": "விளம்பர & சமூக ஊடக குக்கீகள்",
                       "description": "பொருத்தமான விளம்பரங்களைக் காட்ட கூட்டாளர்களால் பயன்படுத்தப்படுகிறது.",
                       "consent_text": "பொருத்தமான விளம்பரங்களைக் காட்ட கூட்டாளர்கள் விளம்பர & சமூக ஊடக குக்கீகளை அமைப்பதற்கு நான் சம்மதிக்கிறேன்."},
                "hi": {"name": "विज्ञापन और सोशल मीडिया कुकीज़",
                       "description": "प्रासंगिक विज्ञापन दिखाने के लिए भागीदारों द्वारा उपयोग किया जाता है।",
                       "consent_text": "मैं प्रासंगिक विज्ञापन दिखाने के लिए भागीदारों द्वारा विज्ञापन और सोशल मीडिया कुकीज़ सेट करने की सहमति देता हूँ।"},
                "kn": {"name": "ಜಾಹೀರಾತು ಮತ್ತು ಸಾಮಾಜಿಕ ಮಾಧ್ಯಮ ಕುಕೀಗಳು",
                       "description": "ಸೂಕ್ತ ಜಾಹೀರಾತುಗಳನ್ನು ತೋರಿಸಲು ಪಾಲುದಾರರು ಬಳಸುತ್ತಾರೆ.",
                       "consent_text": "ಸೂಕ್ತ ಜಾಹೀರಾತುಗಳನ್ನು ತೋರಿಸಲು ಪಾಲುದಾರರು ಜಾಹೀರಾತು ಮತ್ತು ಸಾಮಾಜಿಕ ಮಾಧ್ಯಮ ಕುಕೀಗಳನ್ನು ಹೊಂದಿಸಲು ನಾನು ಸಮ್ಮತಿಸುತ್ತೇನೆ."},
                "ml": {"name": "പരസ്യ & സോഷ്യൽ മീഡിയ കുക്കികൾ",
                       "description": "പ്രസക്തമായ പരസ്യങ്ങൾ കാണിക്കാൻ പങ്കാളികൾ ഉപയോഗിക്കുന്നു.",
                       "consent_text": "പ്രസക്തമായ പരസ്യങ്ങൾ കാണിക്കുന്നതിന് പങ്കാളികൾ പരസ്യ, സോഷ്യൽ മീഡിയ കുക്കികൾ സജ്ജമാക്കുന്നതിന് ഞാൻ സമ്മതിക്കുന്നു."},
                "te": {"name": "ప్రకటనలు & సోషల్ మీడియా కుకీలు",
                       "description": "సంబంధిత ప్రకటనలను చూపించడానికి భాగస్వాములు ఉపయోగిస్తారు.",
                       "consent_text": "సంబంధిత ప్రకటనలను చూపించడానికి భాగస్వాములు ప్రకటనలు & సోషల్ మీడియా కుకీలను సెట్ చేయడానికి నేను సమ్మతిస్తున్నాను."},
            },
        },
    ]
    if db.query(Purpose).count() == 0:
        for spec in purpose_specs:
            purpose = Purpose(
                name=spec["name"], code=spec["code"], description=f"Processing for {spec['name']}",
                legal_basis=spec["legal_basis"], lawful_basis=spec.get("lawful_basis", "CONSENT"),
                requires_consent=spec["requires_consent"],
                retention_period_days=spec["retention"], status="ACTIVE", current_version=1, is_active=True,
            )
            db.add(purpose)
            db.flush()
            db.add(PurposeVersion(
                purpose_id=purpose.id, version_number=1, name=spec["name"],
                description=f"Processing for {spec['name']}",
                legal_basis=spec["legal_basis"], lawful_basis=spec.get("lawful_basis", "CONSENT"),
                requires_consent=spec["requires_consent"],
                retention_period_days=spec["retention"],
                data_category_ids=[categories_by_code[c].id for c in spec["cats"]],
                processing_activity_ids=[activities_by_code[a].id for a in spec["acts"]],
                consent_text=spec["consent_text"], is_current=True, created_by="seed",
                translations=spec["translations"],
            ))
        db.commit()

    # Re-sync translations on existing purpose versions (idempotent re-seed).
    for spec in purpose_specs:
        purpose = db.query(Purpose).filter(Purpose.code == spec["code"]).first()
        if not purpose:
            continue
        pv = consent_service.get_current_purpose_version(purpose)
        if not pv:
            continue
        translations = dict(pv.translations or {})
        for lang_code, lang_copy in spec["translations"].items():
            lang_dict = dict(translations.get(lang_code) or {})
            lang_dict.update(lang_copy)
            translations[lang_code] = lang_dict
        if pv.translations != translations:
            pv.translations = translations
            db.add(pv)
    db.commit()

    # ----------------------------------------------------------------- policies
    def build_standard_rules() -> list[dict]:
        rules = []
        for purpose in db.query(Purpose).order_by(Purpose.code).all():
            pv = consent_service.get_current_purpose_version(purpose)
            if not pv:
                continue
            for cid in pv.data_category_ids:
                dc = db.get(DataCategory, cid)
                for aid in pv.processing_activity_ids:
                    pa = db.get(ProcessingActivity, aid)
                    rules.append({
                        "purpose_code": purpose.code,
                        "data_category_code": dc.code,
                        "processing_activity_code": pa.code,
                        "decision": "ALLOW",
                        "requires_active_consent": purpose.requires_consent,
                        "priority": 10,
                    })
        return rules

    std_policy = db.query(Policy).filter(Policy.code == "std_consent_policy").first()
    if not std_policy:
        std_policy = Policy(name="Standard Consent Policy", code="std_consent_policy",
                            description="Default policy governing consent-based processing.",
                            status="ACTIVE", current_version=1, is_active=True)
        db.add(std_policy)
        db.flush()
        db.add(PolicyVersion(
            policy_id=std_policy.id, version_number=1, rules=build_standard_rules(),
            default_decision="REQUIRE_CONSENT", is_current=True, created_by="seed",
        ))
    else:
        std_version = next((v for v in std_policy.versions if v.is_current), None)
        if std_version:
            std_version.rules = build_standard_rules()
            db.add(std_version)
    db.commit()

    third_policy = db.query(Policy).filter(Policy.code == "third_party_policy").first()
    third_rules = [
        {"purpose_code": "advertising", "data_category_code": "name",
         "processing_activity_code": "third_party_sharing", "decision": "ALLOW",
         "requires_active_consent": True, "priority": 10},
        {"purpose_code": "advertising", "data_category_code": "email",
         "processing_activity_code": "third_party_sharing", "decision": "ALLOW",
         "requires_active_consent": True, "priority": 10},
        {"purpose_code": "advertising", "data_category_code": "phone",
         "processing_activity_code": "third_party_sharing", "decision": "ALLOW",
         "requires_active_consent": True, "priority": 10},
        {"purpose_code": "advertising", "data_category_code": "email",
         "processing_activity_code": "send_marketing_email", "decision": "ALLOW",
         "requires_active_consent": True, "priority": 10},
        {"purpose_code": "advertising", "data_category_code": "phone",
         "processing_activity_code": "send_sms", "decision": "ALLOW",
         "requires_active_consent": True, "priority": 10},
        {"purpose_code": "strictly_necessary", "data_category_code": "identity",
         "processing_activity_code": "fraud_analysis", "decision": "ALLOW",
         "requires_active_consent": False, "priority": 5},
    ]
    if not third_policy:
        third_policy = Policy(name="Third-Party Sharing Policy", code="third_party_policy",
                              description="Strict rules for sharing data with third parties.",
                              status="ACTIVE", current_version=1, is_active=True)
        db.add(third_policy)
        db.flush()
        db.add(PolicyVersion(
            policy_id=third_policy.id, version_number=1, rules=third_rules,
            default_decision="DENY", is_current=True, created_by="seed",
        ))
    else:
        third_version = next((v for v in third_policy.versions if v.is_current), None)
        if third_version:
            third_version.rules = third_rules
            db.add(third_version)
    db.commit()

    # ---------------------------------------------------------------- customers
    if db.query(Customer).count() == 0:
        _customer_specs = [
            ("CUST-10001", "Aarav Patel", "aarav.patel@example.com", "+91-98111-22333", "ACTIVE"),
            ("CUST-10002", "Sanya Iyer", "sanya.iyer@example.com", "+91-98222-33444", "ACTIVE"),
            ("CUST-10003", "Vikram Rao", "vikram.rao@example.com", "+91-98333-44555", "ACTIVE"),
            ("CUST-10004", "Ananya Gupta", "ananya.gupta@example.com", "+91-98444-55666", "ACTIVE"),
            ("CUST-10005", "Rohan Desai", "rohan.desai@example.com", "+91-98555-66777", "SUSPENDED"),
            ("CUST-10006", "Meera Krishnan", "meera.k@example.com", "+91-98666-77888", "ACTIVE"),
            ("CUST-10007", "Kabir Singh", "kabir.singh@example.com", "+91-98777-88999", "ACTIVE"),
            ("CUST-10008", "Ishita Bose", "ishita.bose@example.com", "+91-98888-99000", "ACTIVE"),
        ]
        customers = [
            Customer(
                external_id=eid,
                external_id_search=hmac_digest(eid),
                name=name,
                email=email,
                email_search=hmac_digest(email),
                phone=phone,
                status=status,
                source_app="CRM_APP",
            )
            for eid, name, email, phone, status in _customer_specs
        ]
        db.add_all(customers)
        db.commit()

    # ---------------------------------------------------------- CRM customers
    if db.query(CrmCustomer).count() == 0:
        crm_customers = [
            CrmCustomer(name="Aarav Patel", email="aarav.patel@example.com",
                        email_search=hmac_digest("aarav.patel@example.com"), age=31,
                        aadhar_number="XXXX-XXXX-1234",
                        address="12 MG Road, Bengaluru",
                        phone="+91-98111-22333"),
            CrmCustomer(name="Sanya Iyer", email="sanya.iyer@example.com",
                        email_search=hmac_digest("sanya.iyer@example.com"), age=27,
                        aadhar_number="XXXX-XXXX-5678",
                        address="45 Anna Salai, Chennai",
                        phone="+91-98222-33444"),
            CrmCustomer(name="Vikram Rao", email="vikram.rao@example.com",
                        email_search=hmac_digest("vikram.rao@example.com"), age=34,
                        aadhar_number="XXXX-XXXX-9012",
                        address="8 Connaught Place, New Delhi",
                        phone="+91-98333-44555"),
            CrmCustomer(name="Ananya Gupta", email="ananya.gupta@example.com",
                        email_search=hmac_digest("ananya.gupta@example.com"), age=29,
                        aadhar_number="XXXX-XXXX-3456",
                        address="21 FC Road, Pune",
                        phone="+91-98444-55666"),
            CrmCustomer(name="Meera Krishnan", email="meera.k@example.com",
                        email_search=hmac_digest("meera.k@example.com"), age=42,
                        aadhar_number="XXXX-XXXX-7890",
                        address="77 Marine Drive, Kochi",
                        phone="+91-98666-77888"),
        ]
        db.add_all(crm_customers)
        db.commit()

    # ---------------------------------------------------------------- consents
    customers = db.query(Customer).order_by(Customer.id).all()
    purposes = db.query(Purpose).filter(Purpose.is_active.is_(True)).order_by(Purpose.code).all()
    now = utcnow()
    admin_user = db.query(User).filter(User.username == settings.SEED_ADMIN_USERNAME).first()
    actor = admin_user.username if admin_user else "seed"

    def weighted_status(purpose: Purpose, idx: int) -> str:
        r = random.Random(42 + idx * 7 + purpose.id * 13)
        if not purpose.requires_consent:
            return "ACTIVE"
        roll = r.random()
        if roll < 0.5:
            return "ACTIVE"
        if roll < 0.62:
            return "GRANTED"
        if roll < 0.72:
            return "DENIED"
        if roll < 0.82:
            return "WITHDRAWN"
        if roll < 0.92:
            return "EXPIRED"
        return "PENDING"

    consent_count = 0
    for ci, customer in enumerate(customers):
        for purpose in purposes:
            pv = consent_service.get_current_purpose_version(purpose)
            for cid in pv.data_category_ids:
                dc = db.get(DataCategory, cid)
                for aid in pv.processing_activity_ids:
                    pa = db.get(ProcessingActivity, aid)
                    consent, created = consent_service.get_or_create_consent(
                        db, customer, purpose, dc, pa, actor_username="seed", source_app="CRM_APP",
                        collection_method="UI",
                    )
                    if not created:
                        continue
                    status = weighted_status(purpose, ci)
                    if status == "ACTIVE":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=purpose.retention_period_days,
                            reason="Granted during onboarding", actor_username=actor,
                            source_app="CRM_APP", collection_method="UI",
                            affirmative_action="CLICK",
                        )
                        consent_service.activate_consent(db, consent, actor_username=actor, source_app="CRM_APP")
                        consent.expires_at = now + timedelta(days=random.Random(purpose.id + ci * 5).randint(10, 400))
                    elif status == "GRANTED":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=purpose.retention_period_days,
                            reason="Granted during onboarding", actor_username=actor,
                            source_app="CRM_APP", collection_method="UI",
                            affirmative_action="CLICK",
                        )
                    elif status == "DENIED":
                        consent_service.deny_consent(
                            db, consent, reason="Data principal declined at onboarding",
                            actor_username=actor, source_app="CRM_APP", collection_method="UI",
                        )
                    elif status == "WITHDRAWN":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=purpose.retention_period_days,
                            reason="Granted during onboarding", actor_username=actor,
                            source_app="CRM_APP", collection_method="UI",
                            affirmative_action="CLICK",
                        )
                        consent_service.withdraw_consent(
                            db, consent, reason="Data principal withdrew consent",
                            actor_username=actor, source_app="CRM_APP",
                        )
                    elif status == "EXPIRED":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=0, reason="Granted during onboarding",
                            actor_username=actor, source_app="CRM_APP", collection_method="UI",
                            affirmative_action="CLICK",
                        )
                        consent.expires_at = now - timedelta(days=30)
                        db.commit()
                        consent_service.expire_consents(db, source_app="SYSTEM")
                    else:  # PENDING
                        consent_service.request_consent(
                            db, consent, reason="Consent request sent", actor_username=actor,
                            source_app="CRM_APP", collection_method="UI",
                        )
                        consent.status = "PENDING"
                        db.commit()
                    consent_count += 1

    # ------------------------------------------------------------- audit trail
    from app.services.audit import log_audit
    from app.models.entities import AuditLog

    if db.query(AuditLog).count() < 20:
        for idx, customer in enumerate(customers[:6]):
            purpose = purposes[idx % len(purposes)]
            log_audit(db, "DECISION_EVALUATED", actor_username="integration", source_app="CRM_APP",
                      customer_id=customer.id, customer_external_id=customer.external_id,
                      purpose_id=purpose.id, purpose_code=purpose.code,
                      decision="ALLOW", reason="Valid active consent exists",
                      metadata={"purpose_code": purpose.code, "source": "seed"})
        log_audit(db, "POLICY_EVALUATED", actor_username="system", source_app="SYSTEM",
                  reason="Seeded standard policy applied")

    db.commit()


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed(db)
        print("Seed completed successfully.")
    finally:
        db.close()


if __name__ == "__main__":
    run()
