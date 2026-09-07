/**
 * Strings for the data principal's self-service portal (R2-04).
 *
 * Why this is a SEPARATE catalogue from `translations/types.ts` rather than a
 * fourth section of `PageTranslations`:
 *
 * `PageTranslations` requires every key to be present in all 23 language
 * files or the build fails (see docs/ARCHITECTURE.md). For the three *staff* admin pages
 * that catalogue covers, that is a reasonable forcing function - a staff
 * console with a half-translated screen is a bug someone will fix.
 *
 * This screen is different. It is the one screen in the product a member of
 * the public uses, and the language it is offered in is a statutory duty
 * (DPDP s.5(3) read with the Eighth Schedule), not a nicety. An all-or-nothing
 * per-file requirement pushes in exactly the wrong direction here: it makes
 * adding a single new string a 23-file change, which in practice means either
 * the string does not get added or 22 files get filled with English copied
 * under a native-language key - text that then *looks* translated to the
 * fallback logic and is served to a principal as if it were.
 *
 * So per-language files are `DeepPartial<PortalStrings>` and
 * `getPortalStrings` merges them over English **per key** (not per file, which
 * is what `getTranslations` does). A language that has 90% of the portal
 * translated serves 90% in that language instead of 0%, and a genuinely
 * untranslated key is visibly English rather than silently wrong.
 */

export interface PortalStrings {
  shell: {
    /** Small uppercase eyebrow above the page title. */
    brand: string
    title: string
    signedInAs: string
    skipToContent: string
    languageLabel: string
    sessionExpiresIn: string
    sessionExpiringSoon: string
    sessionExpired: string
    sessionExpiredHelp: string
    returnToSite: string
    minutesShort: string
    close: string
    loading: string
    retry: string
  }
  nav: {
    label: string
    consents: string
    history: string
    receipts: string
    grievances: string
    requests: string
  }
  verify: {
    title: string
    intro: string
    sendCode: string
    sending: string
    sentTitle: string
    sentBody: string
    codeLabel: string
    codeHint: string
    confirm: string
    confirming: string
    resend: string
    whyTitle: string
    whyBody: string
  }
  consents: {
    title: string
    intro: string
    grant: string
    granting: string
    withdraw: string
    withdrawing: string
    grantAll: string
    withdrawAll: string
    withdrawAllBody: string
    bulkDone: string
    symmetryNote: string
    viewNotice: string
    legalBasis: string
    retention: string
    retentionDays: string
    partOfN: string
    empty: string
    gpcNotice: string
    granted: string
    withdrawn: string
    noConsentNeeded: string
  }
  /**
   * R1-09 / R2-11: the two consent moments a principal can be met with when
   * she arrives, both of which are asks rather than announcements.
   *
   * `reConsent` is shown when a purpose she agreed to has changed materially:
   * the decision engine is already refusing to process under the old consent,
   * so the only thing missing is that somebody asks her. `legacyNotice` is the
   * s.5(2) notice for data she consented to before the Act, where processing
   * may lawfully continue *until she withdraws* - which makes the withdrawal
   * button part of the notice, not a link somewhere else.
   *
   * Both are written so that doing nothing is a real option with a stated
   * consequence, and so that refusing costs exactly one press, like agreeing.
   */
  reConsent: {
    bannerTitle: string
    bannerBody: string
    review: string
    dialogTitle: string
    intro: string
    whatChanged: string
    versionLine: string
    blockedNote: string
    agree: string
    agreeing: string
    refuse: string
    refusing: string
    later: string
    laterHelp: string
    equalNote: string
    agreed: string
    refused: string
    allDone: string
  }
  legacyNotice: {
    bannerTitle: string
    bannerBody: string
    read: string
    dialogTitle: string
    receivedOn: string
    continuesUntilWithdrawn: string
    withdrawCta: string
    acknowledge: string
    acknowledging: string
    acknowledged: string
    acknowledgeHelp: string
    noneUnread: string
  }
  notice: {
    title: string
    version: string
    servedIn: string
    servedInFallback: string
    whatWeCollect: string
    necessity: string
    retention: string
    unavailable: string
  }
  history: {
    title: string
    intro: string
    empty: string
    statusChange: string
    recordedBy: string
    evidenceRef: string
    language: string
    gpcRecorded: string
    requestId: string
    messagesTitle: string
    messagesIntro: string
    messagesEmpty: string
    acknowledge: string
    acknowledged: string
    sentOn: string
  }
  receipts: {
    title: string
    intro: string
    empty: string
    issuedOn: string
    action: string
    version: string
    signatureValid: string
    signatureInvalid: string
    signatureValidHelp: string
    signatureInvalidHelp: string
    payloadHash: string
    signature: string
    showDetail: string
    hideDetail: string
    downloadReceipt: string
    exportTitle: string
    exportIntro: string
    exportContains: string
    exportJson: string
    exportCsv: string
    exportPdf: string
    exporting: string
    exportDone: string
    evidenceNote: string
  }
  grievance: {
    title: string
    intro: string
    formTitle: string
    category: string
    categoryHint: string
    subject: string
    subjectHint: string
    description: string
    descriptionHint: string
    aboutPurpose: string
    aboutPurposeNone: string
    submit: string
    submitting: string
    ackTitle: string
    ackReference: string
    ackKeepSafe: string
    ackDueBy: string
    ackResponseDays: string
    ackOfficer: string
    ackBoard: string
    listTitle: string
    listEmpty: string
    raisedOn: string
    dueOn: string
    overdue: string
    daysLeft: string
    viewDetail: string
    hideDetail: string
    timeline: string
    resolution: string
    feedbackTitle: string
    feedbackIntro: string
    feedbackRating: string
    feedbackComment: string
    feedbackSubmit: string
    feedbackDone: string
    escalatedTo: string
    cat: {
      CONSENT_NOT_HONOURED: string
      ACCESS_REQUEST: string
      CORRECTION_REQUEST: string
      ERASURE_REQUEST: string
      NOMINATION: string
      UNAUTHORISED_PROCESSING: string
      EXCESSIVE_COLLECTION: string
      DATA_ACCURACY: string
      SECURITY_INCIDENT: string
      NOTICE_UNCLEAR: string
      OTHER: string
    }
  }
  requests: {
    title: string
    intro: string
    pendingTitle: string
    pendingBody: string
    accessTitle: string
    accessBody: string
    correctionTitle: string
    correctionBody: string
    erasureTitle: string
    erasureBody: string
    nomineeTitle: string
    nomineeBody: string
    meanwhileTitle: string
    meanwhileBody: string
    meanwhileAction: string
    exportAction: string
  }
  rights: {
    panelTitle: string
    panelIntro: string
    contactTitle: string
    dpoRole: string
    email: string
    phone: string
    contactUnavailable: string
    rightsTitle: string
    rAccess: string
    rCorrection: string
    rErasure: string
    rNominate: string
    rGrievance: string
    rWithdraw: string
    respondWithin: string
    linksTitle: string
    linkRights: string
    linkWithdraw: string
    linkGrievance: string
    boardTitle: string
    boardBody: string
    boardLink: string
  }
  errors: {
    generic: string
    expiredLink: string
    missingToken: string
    loadFailed: string
  }
}

type DeepPartial<T> = { [K in keyof T]?: T[K] extends object ? DeepPartial<T[K]> : T[K] }

export type PartialPortalStrings = DeepPartial<PortalStrings>
