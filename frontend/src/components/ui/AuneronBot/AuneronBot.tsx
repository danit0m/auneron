import { useId } from "react";
import { MOOD_LABEL } from "./AuneronBot.mood";
import type { AuneronBotProps, BotMood } from "./AuneronBot.types";
import "./AuneronBot.css";

const cx = (...c: Array<string | false | null | undefined>) => c.filter(Boolean).join(" ");

function Eyes({ mood }: { mood: BotMood }) {
  switch (mood) {
    case "feliz":
    case "celebrando":
      return (
        <g className="ui-bot-eyes">
          <path d="M39 57 Q46 47 53 57" className="ui-bot-stroke" />
          <path d="M67 57 Q74 47 81 57" className="ui-bot-stroke" />
        </g>
      );
    case "dormindo":
      return (
        <g className="ui-bot-eyes">
          <path d="M39 55 Q46 60 53 55" className="ui-bot-stroke" />
          <path d="M67 55 Q74 60 81 55" className="ui-bot-stroke" />
        </g>
      );
    case "critico":
      return (
        <g className="ui-bot-eyes">
          <path d="M37 44 L53 49" className="ui-bot-stroke" />
          <path d="M83 44 L67 49" className="ui-bot-stroke" />
          <rect x="39" y="52" width="13" height="6" rx="3" className="ui-bot-fill ui-bot-blink" />
          <rect x="68" y="52" width="13" height="6" rx="3" className="ui-bot-fill ui-bot-blink" />
        </g>
      );
    case "preocupado":
      return (
        <g className="ui-bot-eyes">
          <path d="M38 47 L52 43" className="ui-bot-stroke" />
          <path d="M82 47 L68 43" className="ui-bot-stroke" />
          <ellipse cx="46" cy="56" rx="5" ry="6" className="ui-bot-fill ui-bot-blink" />
          <ellipse cx="74" cy="56" rx="5" ry="6" className="ui-bot-fill ui-bot-blink" />
        </g>
      );
    case "pensando":
      return (
        <g className="ui-bot-eyes ui-bot-look">
          <circle cx="46" cy="54" r="6.5" className="ui-bot-fill" />
          <circle cx="74" cy="54" r="6.5" className="ui-bot-fill" />
        </g>
      );
    default:
      return (
        <g className="ui-bot-eyes">
          <ellipse cx="46" cy="55" rx="7" ry="8" className="ui-bot-fill ui-bot-blink" />
          <ellipse cx="74" cy="55" rx="7" ry="8" className="ui-bot-fill ui-bot-blink" />
        </g>
      );
  }
}

function Mouth({ mood }: { mood: BotMood }) {
  switch (mood) {
    case "feliz":
      return <path d="M50 67 Q60 75 70 67" className="ui-bot-stroke" />;
    case "celebrando":
      return <path d="M48 66 Q60 80 72 66 Z" className="ui-bot-fill" />;
    case "preocupado":
      return <path d="M49 70 Q54 66 59 70 Q64 74 70 69" className="ui-bot-stroke" />;
    case "critico":
      return <rect x="50" y="68" width="20" height="4" rx="2" className="ui-bot-fill" />;
    case "dormindo":
      return <circle cx="60" cy="70" r="2.5" className="ui-bot-fill" />;
    case "pensando":
      return <path d="M53 70 L67 68" className="ui-bot-stroke" />;
    default:
      return <path d="M53 69 Q60 72 67 69" className="ui-bot-stroke" />;
  }
}

function Extras({ mood }: { mood: BotMood }) {
  switch (mood) {
    case "celebrando":
      return (
        <g className="ui-bot-sparkles">
          <path d="M14 30 l2 5 5 2 -5 2 -2 5 -2 -5 -5 -2 5 -2z" />
          <path d="M104 22 l1.6 4 4 1.6 -4 1.6 -1.6 4 -1.6 -4 -4 -1.6 4 -1.6z" />
          <path d="M108 70 l1.4 3.4 3.4 1.4 -3.4 1.4 -1.4 3.4 -1.4 -3.4 -3.4 -1.4 3.4 -1.4z" />
        </g>
      );
    case "preocupado":
      return <path className="ui-bot-sweat" d="M95 30 Q99 37 95 40 Q91 37 95 30Z" />;
    case "critico":
      return (
        <g>
          <circle className="ui-bot-pulse" cx="60" cy="60" r="50" />
          <text className="ui-bot-bang" x="100" y="30">!</text>
        </g>
      );
    case "pensando":
      return (
        <g className="ui-bot-dots">
          <circle cx="92" cy="18" r="3" />
          <circle cx="102" cy="12" r="3.5" />
          <circle cx="113" cy="6" r="4" />
        </g>
      );
    case "dormindo":
      return (
        <g className="ui-bot-zzz">
          <text x="92" y="26">z</text>
          <text x="101" y="16">z</text>
          <text x="110" y="6">Z</text>
        </g>
      );
    default:
      return null;
  }
}

/** Mascote do assistente de IA do Auneron: um robô de vidro cujo rosto e movimento mostram a situação. */
export function AuneronBot({
  mood = "atento",
  size = "md",
  message,
  label,
  className,
  ...props
}: AuneronBotProps) {
  const text = label ?? MOOD_LABEL[mood];
  const gradientId = `ui-bot-shell-${useId().replace(/[^A-Za-z0-9_-]/g, "")}`;
  return (
    <div className={cx("ui-bot", `ui-bot-${mood}`, `ui-bot-${size}`, className)} {...props}>
      <svg className="ui-bot-figure" viewBox="0 0 120 120" role="img" aria-label={text}>
        <defs>
          <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0" className="ui-bot-shell-top" />
            <stop offset="1" className="ui-bot-shell-bottom" />
          </linearGradient>
        </defs>
        <ellipse className="ui-bot-halo" cx="60" cy="62" rx="44" ry="40" />
        <g className="ui-bot-body">
          <path className="ui-bot-torso" d="M30 120 Q30 94 60 94 Q90 94 90 120 Z" />
          <circle className="ui-bot-core" cx="60" cy="108" r="4" />
          <g className="ui-bot-head">
            <line className="ui-bot-antenna" x1="60" y1="22" x2="60" y2="11" />
            <circle className="ui-bot-antenna-tip" cx="60" cy="9" r="4.5" />
            <rect className="ui-bot-ear" x="9" y="45" width="11" height="24" rx="5.5" />
            <rect className="ui-bot-ear" x="100" y="45" width="11" height="24" rx="5.5" />
            <rect className="ui-bot-shell" fill={`url(#${gradientId})`} x="17" y="22" width="86" height="68" rx="30" />
            <rect className="ui-bot-visor" x="27" y="35" width="66" height="45" rx="21" />
            <Eyes mood={mood} />
            <Mouth mood={mood} />
          </g>
        </g>
        <Extras mood={mood} />
      </svg>
      <p className="ui-bot-message">{message ?? text}</p>
    </div>
  );
}
