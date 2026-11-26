// Logotipos oficiais da identidade D2 (Marketing). Os arquivos ficam em public/brand e nao devem
// ser recoloridos, deformados ou receber efeitos. O tema efetivo escolhe a versao clara ou escura
// pelo CSS em styles/brand.css.
type BrandLogoProps = {
  variant: "horizontal" | "symbol";
  className?: string;
};

const NOME = "AUNERON Digital Worker";

export function BrandLogo({ variant, className }: BrandLogoProps) {
  if (variant === "symbol") {
    return (
      <img
        className={["brand-symbol", className].filter(Boolean).join(" ")}
        src="/brand/05_simbolo_transparente.svg"
        alt={NOME}
        width={44}
        height={44}
      />
    );
  }

  return (
    <span className={["brand-horizontal", className].filter(Boolean).join(" ")}>
      <img
        className="brand-horizontal-light"
        src="/brand/04_horizontal_transparente_claro.svg"
        alt={NOME}
        width={260}
        height={78}
      />
      <img
        className="brand-horizontal-dark"
        src="/brand/03_horizontal_transparente_escuro.svg"
        alt=""
        aria-hidden="true"
        width={260}
        height={78}
      />
    </span>
  );
}
