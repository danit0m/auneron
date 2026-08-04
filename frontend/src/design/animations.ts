export const duration = {
  instant: "0ms",
  fast: "120ms",
  normal: "180ms",
  slow: "280ms",
  slower: "450ms",
} as const;

export const easing = {
  linear: "linear",
  standard: "cubic-bezier(0.2, 0, 0, 1)",
  emphasized: "cubic-bezier(0.2, 0, 0, 1.2)",
  entrance: "cubic-bezier(0, 0, 0.2, 1)",
  exit: "cubic-bezier(0.4, 0, 1, 1)",
} as const;

export const transitions = {
  colors: `color ${duration.normal} ${easing.standard}, background-color ${duration.normal} ${easing.standard}, border-color ${duration.normal} ${easing.standard}`,
  transform: `transform ${duration.normal} ${easing.standard}`,
  opacity: `opacity ${duration.normal} ${easing.standard}`,
  shadow: `box-shadow ${duration.normal} ${easing.standard}`,
  all: `all ${duration.normal} ${easing.standard}`,
} as const;

export const keyframes = {
  fadeIn: {
    from: { opacity: 0 },
    to: { opacity: 1 },
  },

  fadeOut: {
    from: { opacity: 1 },
    to: { opacity: 0 },
  },

  slideUp: {
    from: {
      opacity: 0,
      transform: "translateY(0.75rem)",
    },
    to: {
      opacity: 1,
      transform: "translateY(0)",
    },
  },

  scaleIn: {
    from: {
      opacity: 0,
      transform: "scale(0.98)",
    },
    to: {
      opacity: 1,
      transform: "scale(1)",
    },
  },

  spin: {
    to: {
      transform: "rotate(360deg)",
    },
  },

  pulse: {
    "0%, 100%": {
      opacity: 1,
    },
    "50%": {
      opacity: 0.55,
    },
  },
} as const;

export const motion = {
  modalEnter: {
    duration: duration.normal,
    easing: easing.entrance,
  },

  modalExit: {
    duration: duration.fast,
    easing: easing.exit,
  },

  hoverLift: {
    transform: "translateY(-1px)",
    transition: `${transitions.transform}, ${transitions.shadow}`,
  },

  reducedMotionQuery: "(prefers-reduced-motion: reduce)",
} as const;

export type Animations = {
  duration: typeof duration;
  easing: typeof easing;
  transitions: typeof transitions;
  keyframes: typeof keyframes;
  motion: typeof motion;
};
