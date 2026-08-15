export const VIEWPORT_BREAKPOINTS = Object.freeze({
  layoutNarrowWidth: 980,
  consoleMobileWidth: 860,
  compactWidth: 640,
  consoleShortHeight: 700,
});

export const VIEWPORT_QUERIES = Object.freeze({
  layoutNarrow: `(max-width: ${VIEWPORT_BREAKPOINTS.layoutNarrowWidth}px)`,
  consoleMobile: `(max-width: ${VIEWPORT_BREAKPOINTS.consoleMobileWidth}px)`,
  compact: `(max-width: ${VIEWPORT_BREAKPOINTS.compactWidth}px)`,
  consoleShort: `(max-width: ${VIEWPORT_BREAKPOINTS.consoleMobileWidth}px) and (max-height: ${VIEWPORT_BREAKPOINTS.consoleShortHeight}px)`,
});

export const VIEWPORT_ROOT_CLASSES = Object.freeze({
  layoutNarrow: 'bp-layout-narrow',
  consoleMobile: 'bp-console-mobile',
  compact: 'bp-compact',
  consoleShort: 'bp-console-short',
});

type ViewportFlag = keyof typeof VIEWPORT_QUERIES;

type Cleanup = () => void;
type LegacyMediaQueryList = MediaQueryList & {
  addListener: (listener: (event: MediaQueryListEvent) => void) => void;
  removeListener: (listener: (event: MediaQueryListEvent) => void) => void;
};

export function installViewportRootClasses(target: HTMLElement = document.documentElement): Cleanup {
  if (typeof window === 'undefined') {
    return () => {};
  }

  const flags = Object.keys(VIEWPORT_QUERIES) as ViewportFlag[];
  const registrations = flags.map((flag) => {
    const mediaQuery = window.matchMedia(VIEWPORT_QUERIES[flag]);
    const className = VIEWPORT_ROOT_CLASSES[flag];

    const sync = (): void => {
      target.classList.toggle(className, mediaQuery.matches);
    };

    sync();

    const listener = (): void => sync();
    if ('addEventListener' in mediaQuery) {
      mediaQuery.addEventListener('change', listener);
    } else {
      (mediaQuery as LegacyMediaQueryList).addListener(listener);
    }

    return { className, listener, mediaQuery };
  });

  return () => {
    registrations.forEach(({ className, listener, mediaQuery }) => {
      if ('removeEventListener' in mediaQuery) {
        mediaQuery.removeEventListener('change', listener);
      } else {
        (mediaQuery as LegacyMediaQueryList).removeListener(listener);
      }
      target.classList.remove(className);
    });
  };
}
