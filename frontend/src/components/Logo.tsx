/**
 * Consent 360 brand mark — renders the company Bildmarke asset.
 * Shown on a white rounded tile so it reads cleanly on any surface.
 */
export function Consent360Logo({ size = 18 }: { size?: number }) {
  return (
    <img
      src="/consent-mark.jpg"
      alt="Consent 360"
      width={size}
      height={size}
      style={{ objectFit: 'cover', borderRadius: 'inherit', display: 'block' }}
    />
  )
}