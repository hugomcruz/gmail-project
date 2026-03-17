interface Props {
  /** Icon box size */
  size?: 'sm' | 'md' | 'lg'
  /** Show the text title next to / below the icon */
  withText?: boolean
  /** Layout: 'row' puts icon + text side by side, 'col' stacks them */
  layout?: 'row' | 'col'
}

const sizes = {
  sm:  { box: 'w-8 h-8',   icon: 18, title: 'text-sm',   sub: 'text-xs' },
  md:  { box: 'w-10 h-10', icon: 22, title: 'text-base',  sub: 'text-xs' },
  lg:  { box: 'w-16 h-16', icon: 32, title: 'text-2xl',   sub: 'text-sm' },
}

/**
 * HEAT Email Processor — brand icon.
 *
 * Visual mashup:
 *  • Envelope body with rounded corners  (universal email / Outlook-style)
 *  • Gmail-style M-chevron fold on the flap
 *  • Gear badge bottom-right              (the "processor" / automation)
 *
 * Background gradient blends Gmail-red → Outlook-blue via purple.
 */
function HeatIcon({ size }: { size: number }) {
  // All coordinates are in a 24×24 viewBox; scale via width/height props.
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      xmlns="http://www.w3.org/2000/svg"
      aria-label="HEAT Email Processor"
    >
      {/* ── Envelope body ─────────────────────────────────────── */}
      <rect
        x="1.25" y="3.75"
        width="17.5" height="13"
        rx="2"
        stroke="white" strokeWidth="1.4"
        strokeLinejoin="round"
      />

      {/* ── Gmail-style M-chevron (envelope flap fold) ────────── */}
      <path
        d="M1.25 6.25 L5.5 10 L10 6.75 L14.5 10 L19 6.25"
        stroke="white" strokeWidth="1.4"
        strokeLinecap="round" strokeLinejoin="round"
      />

      {/* ── Gear badge — sits bottom-right, overlaps envelope ─── */}
      {/* Dark backdrop circle so gear reads clearly */}
      <circle cx="18.5" cy="14.5" r="4.75" fill="rgba(0,0,0,0.35)" />
      {/* Outer gear ring — dashed stroke creates the teeth effect */}
      <circle
        cx="18.5" cy="14.5" r="3.5"
        stroke="white" strokeWidth="1.6"
        strokeDasharray="2.1 1.55"
        strokeLinecap="round"
      />
      {/* Inner hub */}
      <circle cx="18.5" cy="14.5" r="1.55" fill="white" />
    </svg>
  )
}

export default function HeatLogo({ size = 'md', withText = false, layout = 'row' }: Props) {
  const s = sizes[size]

  const iconBox = (
    <div className={`${s.box} shrink-0 rounded-xl bg-gradient-to-br from-blue-600 via-purple-600 to-rose-500 flex items-center justify-center shadow-lg shadow-purple-900/40`}>
      <HeatIcon size={s.icon} />
    </div>
  )

  if (!withText) return iconBox

  const text = (
    <div className={layout === 'col' ? 'text-center' : ''}>
      <p className={`${s.title} font-bold text-white leading-tight tracking-tight`}>HEAT</p>
      <p className="text-xs text-gray-400 font-normal leading-tight mt-0.5">Email Processor</p>
    </div>
  )

  if (layout === 'col') {
    return (
      <div className="flex flex-col items-center gap-3">
        {iconBox}
        {text}
      </div>
    )
  }

  return (
    <div className="flex items-center gap-3">
      {iconBox}
      {text}
    </div>
  )
}

