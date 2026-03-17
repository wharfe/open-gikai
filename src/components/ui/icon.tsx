type IconProps = {
  name: string;
  size?: number;
  className?: string;
};

/**
 * Material Symbols Rounded icon component.
 * @see https://fonts.google.com/icons
 */
export function Icon({ name, size = 24, className = "" }: IconProps) {
  return (
    <span
      className={`material-symbols-rounded ${className}`}
      style={{ fontSize: size }}
      aria-hidden="true"
    >
      {name}
    </span>
  );
}
