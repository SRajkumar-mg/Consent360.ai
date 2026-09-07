"""Seed script - populates roles, users, reference data, purposes, policies,
customers, consents and audit history so the platform looks functional immediately.

Carried item (task 6): by default this creates seven demo staff users with
fixed, published passwords (this file, README, PROJECT.md all name them) and
eight demo Customer/CrmCustomer rows with fake people's names, emails and
phone numbers. That is fine for a dev/demo install but must never happen to
a production one - a production install that ran the same seed script would
inherit the exact same publicly-known admin password as every other install
of this platform. Pass ``--production`` (or set ``SEED_PROFILE=production``)
to seed only real configuration (roles, reference data, purposes, policies,
notices) plus exactly one admin user with a random, one-time-printed
password (or an operator-supplied ``SEED_ADMIN_PASSWORD`` that is not the
demo default) - no other demo users, no demo customers, no demo consents, no
synthetic audit history.
"""
import argparse
import os
import random
import secrets
import string
from datetime import datetime, timedelta, timezone

from sqlalchemy.orm import Session

from app.core.database import Base, SessionLocal, engine
from app.core.rbac import ROLE_DESCRIPTIONS, ROLE_PERMISSIONS
from app.core.security import hash_password
from app.core.config import get_settings
from app.models.entities import (
    Consent,
    ConsentEvidence,
    ConsentHistory,
    CrmCustomer,
    Customer,
    DataCategory,
    Notice,
    NoticeVersion,
    Organization,
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
from app.services.tenancy import platform_tenant_id, resolve_tenant_id

settings = get_settings()

# The fixed password Settings.SEED_ADMIN_PASSWORD defaults to (and the one
# this file, README.md and PROJECT.md all publish) - a production seed run
# with a SEED_ADMIN_PASSWORD still equal to this is treated as "not actually
# customised" and gets a generated password instead. See seed_admin_credentials().
_DEMO_ADMIN_PASSWORD_DEFAULT = "Admin@1234"


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def is_production_profile(argv: list[str] | None = None) -> bool:
    """True when this run should seed a production profile (see this
    module's docstring): ``SEED_PROFILE=production`` in the environment, or
    ``--production`` on the command line."""
    if os.environ.get("SEED_PROFILE", "").strip().lower() == "production":
        return True
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--production", action="store_true")
    args, _unknown = parser.parse_known_args(argv)
    return args.production


def _generate_strong_password(length: int = 24) -> str:
    alphabet = string.ascii_letters + string.digits + "!@#$%^&*()-_=+"
    return "".join(secrets.choice(alphabet) for _ in range(length))


def seed_admin_credentials() -> tuple[str, bool]:
    """Returns (password, was_generated) for the single production admin
    account. Uses SEED_ADMIN_PASSWORD from the environment only when an
    operator has actually changed it away from the published demo default;
    otherwise generates a fresh, random one so a production install can
    never end up with the same admin password as every demo install of this
    platform."""
    configured = settings.SEED_ADMIN_PASSWORD
    if configured and configured != _DEMO_ADMIN_PASSWORD_DEFAULT:
        return configured, False
    return _generate_strong_password(), True


def seed(db: Session, *, production: bool = False) -> None:
    # ------------------------------------------------------------------ roles
    for name, perms in ROLE_PERMISSIONS.items():
        existing = db.query(Role).filter(Role.name == name).first()
        if existing:
            if set(existing.permissions) != set(perms):
                existing.permissions = perms
        else:
            db.add(Role(name=name, description=ROLE_DESCRIPTIONS.get(name, ""), permissions=perms, is_system=True))
    db.commit()

    # ------------------------------------------------------ retention floors
    # Same idiom as the role-permission re-sync just above: R1-10's floors
    # live in code (app/services/retention.py::RETENTION_CLASSES), and
    # `retention_schedules` rows already seeded by an earlier migration do
    # not move on their own when a floor is later raised in code (CM-03
    # raised data_sharing_events from 1 year to 7). `resync_schedule_floors`
    # raises any such row up to the floor now in force - and only ever up,
    # never down - so re-running this script after a floor change is what
    # brings an already-deployed schedule back into compliance. Deliberately
    # NOT done inside retention_scan/ensure_schedule_rows themselves: a scan
    # that silently fixed drift on read would defeat the very check
    # tests/test_retention_and_evidence_pack.py proves works (a schedule
    # written below its floor - by bypass or by a floor raised after the
    # fact - must be reported, not quietly corrected).
    from app.services.retention import resync_schedule_floors

    resync_schedule_floors(db, actor_username="seed")

    # Remap legacy roles onto the two current roles, then drop the old ones.
    legacy_role_map = {
        "consent_admin": "admin",
        "privacy_officer": "admin",
        "data_steward": "admin",
        "customer_service": "consent_manager",
        "auditor": "consent_manager",
        "read_only": "consent_manager",
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
        manager_role = db.query(Role).filter(Role.name == "consent_manager").first()
        viewer_role = db.query(Role).filter(Role.name == "viewer").first()

        if production:
            admin_password, generated = seed_admin_credentials()
            admin_email = os.environ.get("SEED_ADMIN_EMAIL", "").strip() or "admin@consent.local"
            users = [
                User(username=settings.SEED_ADMIN_USERNAME, full_name="System Administrator",
                     email=admin_email, password_hash=hash_password(admin_password),
                     role_id=admin_role.id, is_active=True),
            ]
            db.add_all(users)
            db.commit()
            if generated:
                # The ONLY place this password is ever shown - it is never
                # logged, stored in a table, or written to a file by this
                # script. If the operator loses it, reset it directly
                # (change the password hash, or set SEED_ADMIN_PASSWORD to a
                # real value and re-run) rather than re-running seed.py,
                # which is idempotent and will not touch an existing user.
                print("=" * 72)
                print(f"Production admin user '{settings.SEED_ADMIN_USERNAME}' created with a GENERATED password:")
                print(f"    {admin_password}")
                print("Store this now - it is not saved anywhere and will not be shown again.")
                print("=" * 72)
            else:
                print(f"Production admin user '{settings.SEED_ADMIN_USERNAME}' created using SEED_ADMIN_PASSWORD from the environment.")
        else:
            users = [
                User(username=settings.SEED_ADMIN_USERNAME, full_name="System Administrator",
                     email="admin@consent.local", password_hash=hash_password(settings.SEED_ADMIN_PASSWORD),
                     role_id=admin_role.id, is_active=True),
                User(username="privacy.officer", full_name="Priya Nair", email="privacy@consent.local",
                     password_hash=hash_password("Privacy@1234"), role_id=admin_role.id, is_active=True),
                User(username="data.steward", full_name="Arjun Mehta", email="steward@consent.local",
                     password_hash=hash_password("Steward@1234"), role_id=admin_role.id, is_active=True),
                User(username="customer.service", full_name="Kavya Sharma", email="service@consent.local",
                     password_hash=hash_password("Service@1234"), role_id=manager_role.id, is_active=True),
                User(username="auditor", full_name="Rahul Verma", email="auditor@consent.local",
                     password_hash=hash_password("Auditor@1234"), role_id=manager_role.id, is_active=True),
                User(username="readonly", full_name="Inspect User", email="readonly@consent.local",
                     password_hash=hash_password("Readonly@1234"), role_id=manager_role.id, is_active=True),
                User(username="viewer", full_name="View Only", email="viewer@consent.local",
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
            # R1-05/B-06: DPDP has no "legitimate interest" gateway. Strictly
            # necessary processing (login/security/consent-record keeping) is
            # voluntarily provided by the principal by using the portal, and
            # processed unless/until she indicates otherwise - s.7(a).
            "legal_basis": "S7_A", "retention": 365, "requires_consent": False,
            "cats": ["identity", "device"], "acts": ["customer_support", "fraud_analysis", "communication"],
            "services_enabled": "Portal login, session security, fraud prevention and keeping "
                                 "a record of your consent choices.",
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
            "services_enabled": "Remembering your language and personalisation preferences and "
                                 "showing you personalised offers.",
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
            # "research" (product/market research) is included alongside
            # "analytics" so every seeded ProcessingActivity has at least one
            # active purpose with a valid gateway - see K-10/L-01 coverage.
            "cats": ["behavioral", "device", "location"], "acts": ["analytics", "research"],
            "services_enabled": "Understanding how the platform is used, product analytics and "
                                 "research to improve the service.",
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
            "services_enabled": "Showing relevant advertising and sharing data with advertising "
                                 "and social media partners.",
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
    def _data_items_for(spec: dict) -> list[dict]:
        """Itemised data_items (A-01/B-04) derived from a purpose_spec's data
        categories: one entry per category actually used by this purpose,
        marked necessary (none of the seeded purposes collect "nice to have"
        extras beyond what they list)."""
        return [
            {
                "data_category_id": categories_by_code[c].id,
                "necessity": True,
                "description": f"{categories_by_code[c].name} used for {spec['name'].lower()}.",
            }
            for c in spec["cats"]
        ]

    if db.query(Purpose).count() == 0:
        for spec in purpose_specs:
            purpose = Purpose(
                name=spec["name"], code=spec["code"], description=f"Processing for {spec['name']}",
                legal_basis=spec["legal_basis"], requires_consent=spec["requires_consent"],
                retention_period_days=spec["retention"],
                services_enabled=spec.get("services_enabled", ""),
                status="ACTIVE", current_version=1, is_active=True,
                tenant_id=platform_tenant_id(db),
            )
            db.add(purpose)
            db.flush()
            db.add(PurposeVersion(
                purpose_id=purpose.id, version_number=1, name=spec["name"],
                description=f"Processing for {spec['name']}",
                legal_basis=spec["legal_basis"], requires_consent=spec["requires_consent"],
                retention_period_days=spec["retention"],
                data_category_ids=[categories_by_code[c].id for c in spec["cats"]],
                processing_activity_ids=[activities_by_code[a].id for a in spec["acts"]],
                data_items=_data_items_for(spec),
                services_enabled=spec.get("services_enabled", ""),
                consent_text=spec["consent_text"], is_current=True, created_by="seed",
                translations=spec["translations"],
            ))
        db.commit()

    # Re-sync lawful basis / itemisation / activity coverage on an
    # already-seeded database (idempotent re-seed): the purpose-creation
    # block above only runs once, so a database seeded before this task
    # (legal_basis="LEGITIMATE_INTEREST", no "research" activity coverage)
    # needs its *current* PurposeVersion re-versioned to the corrected
    # spec - exactly what PUT /purposes/{id} would do for the same change,
    # so past consents keep pointing at their original (uncorrected) pinned
    # version (R1-05/B-06/L-01/K-10).
    for spec in purpose_specs:
        purpose = db.query(Purpose).filter(Purpose.code == spec["code"]).first()
        if not purpose:
            continue
        pv = consent_service.get_current_purpose_version(purpose)
        if not pv:
            continue
        desired_activity_ids = [activities_by_code[a].id for a in spec["acts"]]
        desired_data_items = _data_items_for(spec)
        desired_services_enabled = spec.get("services_enabled", "")
        changed = (
            pv.legal_basis != spec["legal_basis"]
            or sorted(pv.processing_activity_ids or []) != sorted(desired_activity_ids)
            or (pv.data_items or []) != desired_data_items
            or (pv.services_enabled or "") != desired_services_enabled
        )
        if not changed:
            continue
        pv.is_current = False
        pv.effective_to = utcnow()
        new_pv = PurposeVersion(
            purpose_id=purpose.id, version_number=pv.version_number + 1, name=pv.name,
            description=pv.description, legal_basis=spec["legal_basis"],
            requires_consent=pv.requires_consent, retention_period_days=pv.retention_period_days,
            data_category_ids=pv.data_category_ids, processing_activity_ids=desired_activity_ids,
            data_items=desired_data_items, services_enabled=desired_services_enabled,
            child_restricted=pv.child_restricted, retention_policy_id=pv.retention_policy_id,
            consent_text=pv.consent_text, translations=pv.translations, is_current=True, created_by="seed",
        )
        db.add(new_pv)
        db.flush()
        purpose.legal_basis = new_pv.legal_basis
        purpose.services_enabled = new_pv.services_enabled
        purpose.current_version = new_pv.version_number
        db.add(purpose)
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

    # ------------------------------------------------------------------ notices
    # R1-04: publish a Notice for every seeded purpose, so the demo satisfies
    # its own definition of done - "a notice version can be published ... and
    # is referenced by every consent recorded after publication" - out of the
    # box, and the coverage/evidence-completeness KPIs have something real to
    # report on immediately after seeding rather than only after an admin
    # manually publishes one.
    import hashlib as _hashlib
    import json as _json

    def _notice_content_hash(nv: NoticeVersion) -> str:
        canonical = {
            "notice_id": nv.notice_id, "version_number": nv.version_number,
            "language_default": nv.language_default, "title": nv.title, "body": nv.body,
            "translations": nv.translations or {}, "data_items": nv.data_items or [],
            "purposes": nv.purposes or [], "services_enabled": nv.services_enabled or "",
            "retention_period_days": nv.retention_period_days, "retention_note": nv.retention_note or "",
            "child_restricted": nv.child_restricted, "links": nv.links or {},
            "contact_snapshot": nv.contact_snapshot or {},
        }
        return _hashlib.sha256(_json.dumps(canonical, sort_keys=True, default=str).encode("utf-8")).hexdigest()

    platform_org = db.get(Organization, platform_tenant_id(db))
    for spec in purpose_specs:
        purpose = db.query(Purpose).filter(Purpose.code == spec["code"]).first()
        if not purpose:
            continue
        pv = consent_service.get_current_purpose_version(purpose)
        if not pv:
            continue
        notice = db.query(Notice).filter(Notice.purpose_id == purpose.id).first()
        if not notice:
            notice = Notice(tenant_id=platform_tenant_id(db), purpose_id=purpose.id, status="DRAFT", current_version=0)
            db.add(notice)
            db.flush()
        if any(v.is_current for v in notice.versions):
            continue  # already published (e.g. re-running seed.py) - leave it alone

        next_version = max([v.version_number for v in notice.versions], default=0) + 1
        translations = {
            lang: {"title": copy.get("name", ""), "body": copy.get("consent_text") or copy.get("description", "")}
            for lang, copy in (spec.get("translations") or {}).items()
        }
        nv = NoticeVersion(
            notice_id=notice.id, version_number=next_version, language_default="en",
            title=f"Notice: {purpose.name}",
            body=pv.consent_text or f"We process your data for {purpose.name.lower()}.",
            translations=translations, data_items=list(pv.data_items or []),
            services_enabled=pv.services_enabled or "", retention_period_days=purpose.retention_period_days,
            retention_note=f"Retained for {purpose.retention_period_days} days.",
            child_restricted=purpose.child_restricted, created_by="seed",
        )
        db.add(nv)
        db.flush()
        now = utcnow()
        nv.purposes = [{
            "code": purpose.code, "name": purpose.name, "description": pv.description,
            "legal_basis": purpose.legal_basis, "requires_consent": purpose.requires_consent,
            "retention_period_days": purpose.retention_period_days,
        }]
        nv.links = {
            "withdraw_url": platform_org.withdraw_url if platform_org else "",
            "rights_url": platform_org.rights_url if platform_org else "",
            "grievance_url": platform_org.grievance_url if platform_org else "",
            "board_complaint_url": platform_org.board_complaint_url if platform_org else "",
        }
        nv.contact_snapshot = {
            "dpo_name": platform_org.dpo_name if platform_org else "",
            "dpo_email": platform_org.dpo_email if platform_org else "",
            "dpo_phone": platform_org.dpo_phone if platform_org else "",
        }
        nv.content_hash = _notice_content_hash(nv)
        nv.is_current = True
        nv.effective_from = now
        nv.published_by = "seed"
        nv.published_at = now
        notice.status = "ACTIVE"
        notice.current_version = next_version
        db.add(notice)
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
                            status="ACTIVE", current_version=1, is_active=True,
                            tenant_id=platform_tenant_id(db))
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
                              status="ACTIVE", current_version=1, is_active=True,
                              tenant_id=platform_tenant_id(db))
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
    # R1-12/task-6: no demo person data (fake customers) in a production
    # profile - consents/audit-trail seeding below only ever touches
    # whatever Customer rows exist, so skipping creation here also empties
    # those out with no further conditionals needed.
    if not production and db.query(Customer).count() == 0:
        seed_customer_tenant_id = resolve_tenant_id(db, "CRM_APP")
        customers = [
            Customer(external_id="CUST-10001", name="Aarav Patel", email="aarav.patel@example.com",
                     phone="+91-98111-22333", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10002", name="Sanya Iyer", email="sanya.iyer@example.com",
                     phone="+91-98222-33444", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10003", name="Vikram Rao", email="vikram.rao@example.com",
                     phone="+91-98333-44555", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10004", name="Ananya Gupta", email="ananya.gupta@example.com",
                     phone="+91-98444-55666", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10005", name="Rohan Desai", email="rohan.desai@example.com",
                     phone="+91-98555-66777", status="SUSPENDED", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10006", name="Meera Krishnan", email="meera.k@example.com",
                     phone="+91-98666-77888", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10007", name="Kabir Singh", email="kabir.singh@example.com",
                     phone="+91-98777-88999", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
            Customer(external_id="CUST-10008", name="Ishita Bose", email="ishita.bose@example.com",
                     phone="+91-98888-99000", status="ACTIVE", source_app="CRM_APP",
                     tenant_id=seed_customer_tenant_id),
        ]
        db.add_all(customers)
        db.commit()

    # ---------------------------------------------------------- CRM customers
    # aadhar_number removed (R1-12/H-11) - see the same note on
    # app.models.entities.CrmCustomer.
    if not production and db.query(CrmCustomer).count() == 0:
        crm_customers = [
            CrmCustomer(name="Aarav Patel", email="aarav.patel@example.com", age=31,
                        address="12 MG Road, Bengaluru", phone="+91-98111-22333"),
            CrmCustomer(name="Sanya Iyer", email="sanya.iyer@example.com", age=27,
                        address="45 Anna Salai, Chennai", phone="+91-98222-33444"),
            CrmCustomer(name="Vikram Rao", email="vikram.rao@example.com", age=34,
                        address="8 Connaught Place, New Delhi", phone="+91-98333-44555"),
            CrmCustomer(name="Ananya Gupta", email="ananya.gupta@example.com", age=29,
                        address="21 FC Road, Pune", phone="+91-98444-55666"),
            CrmCustomer(name="Meera Krishnan", email="meera.k@example.com", age=42,
                        address="77 Marine Drive, Kochi", phone="+91-98666-77888"),
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
                        )
                        consent_service.activate_consent(db, consent, actor_username=actor, source_app="CRM_APP")
                        consent.expires_at = now + timedelta(days=random.Random(purpose.id + ci * 5).randint(10, 400))
                    elif status == "GRANTED":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=purpose.retention_period_days,
                            reason="Granted during onboarding", actor_username=actor,
                            source_app="CRM_APP", collection_method="UI",
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
                        )
                        consent_service.withdraw_consent(
                            db, consent, reason="Data principal withdrew consent",
                            actor_username=actor, source_app="CRM_APP",
                        )
                    elif status == "EXPIRED":
                        consent_service.grant_consent(
                            db, consent, expires_in_days=0, reason="Granted during onboarding",
                            actor_username=actor, source_app="CRM_APP", collection_method="UI",
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
    # No synthetic audit history in a production profile either - a
    # production audit_logs table's chain should start from real activity,
    # not seeded demo events.
    if not production:
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

    # ---------------------------------------------------- notification templates
    # R3-06/O-01: real configuration, not demo/person data - seeded in every
    # profile (including production). English copy only here, reusing the
    # exact same fallback templates app/services/notifications.py falls back
    # to when no row exists yet, so there is one authored copy of each
    # message, not two that can drift. Authoring the remaining 22 languages'
    # copy is a translation/content task for whoever owns localisation (the
    # same kind of one-off generation docs/ARCHITECTURE.md notes for the frontend's own
    # translations/_gen*.py), not something this seed script does - the
    # `language` column and its CHECK constraint already make every one of
    # them a legal, structurally-supported row once that copy exists.
    from app.models.entities import NotificationTemplate
    from app.services.notifications import FALLBACK_TEMPLATES

    if db.query(NotificationTemplate).count() == 0:
        for event_type, by_channel in FALLBACK_TEMPLATES.items():
            for channel, (subject, body) in by_channel.items():
                db.add(NotificationTemplate(
                    tenant_id=None, event_type=event_type, channel=channel, language="en",
                    subject=subject, body_template=body, is_active=True, created_by="seed",
                ))
        db.commit()


def run(argv: list[str] | None = None):
    production = is_production_profile(argv)
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        seed(db, production=production)
        print(f"Seed completed successfully ({'production' if production else 'demo'} profile).")
    finally:
        db.close()


if __name__ == "__main__":
    run()
