// The ROOKS mark: a geometric chess rook (battlements, tapered tower, base). Inherits the text colour.
export const ROOK_PATH = "M9 6h3v3h2.5V6h3v3H20V6h3v6H9z M11.5 13h9l1 9h-11z M8.5 23h15v3h-15z";

export default function RookMark({ size = 16, title }: { size?: number; title?: string }) {
  return (
    <svg width={size} height={size} viewBox="4 4 24 24" fill="currentColor" role={title ? "img" : undefined}
      aria-hidden={title ? undefined : true} aria-label={title}>
      <path d={ROOK_PATH} fillRule="evenodd" />
    </svg>
  );
}
