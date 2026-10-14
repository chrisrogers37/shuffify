// One token source feeds Tailwind utilities and the shared CSS custom properties.
// This adapter runs inside the existing standalone Tailwind CLI; no build package
// or additional runtime is required.
const tokens = require('./tokens.json');
const fs = require('fs');
const path = require('path');

// Bad aliases otherwise become undefined utilities or unresolved CSS variables.
// Font URLs resolve from the generated stylesheet, never from this adapter.
function validateTokens(values, staticRoot = path.resolve(__dirname, '../../shuffify/static')) {
  Object.entries(values.spacing.semantic).forEach(([name, scale]) => {
    if (!Object.hasOwn(values.spacing.scale, scale)) {
      throw new Error(`Unknown spacing alias: ${name} -> ${scale}`);
    }
  });
  Object.entries(values.fonts.roles).forEach(([name, font]) => {
    if (!Array.isArray(font.family) || !font.family.length ||
        font.family.some((family) => typeof family !== 'string' || !family.trim())) {
      throw new Error(`Invalid font family: ${name}`);
    }
    if (typeof font.source !== 'string' || !font.source.endsWith('.woff2')) {
      throw new Error(`Invalid font source: ${name}`);
    }
    const asset = path.resolve(staticRoot, 'css', font.source);
    const relative = path.relative(path.resolve(staticRoot, 'fonts'), asset);
    if (relative.startsWith('..') || path.isAbsolute(relative) ||
        !fs.existsSync(asset) || !fs.statSync(asset).isFile()) {
      throw new Error(`Missing or non-local font asset: ${name} -> ${font.source}`);
    }
  });
}

validateTokens(tokens);

const namespaced = (values) => Object.fromEntries(
  Object.entries(values).map(([name, value]) => [`sh-${name}`, value])
);
const fontStack = (family) => family.map((name) =>
  name === 'sans-serif' ? name : JSON.stringify(name)
).join(', ');
const spacing = {
  ...tokens.spacing.scale,
  ...Object.fromEntries(Object.entries(tokens.spacing.semantic).map(
    ([name, scale]) => [name, tokens.spacing.scale[scale]]
  )),
};

const theme = {
  screens: namespaced(tokens.breakpoints),
  colors: { sh: tokens.colors },
  fontFamily: namespaced(Object.fromEntries(Object.entries(tokens.fonts.roles).map(
    ([name, font]) => [name, font.family]
  ))),
  fontSize: namespaced(Object.fromEntries(Object.entries(tokens.type.scale).map(
    ([name, type]) => [name, [type.size, { lineHeight: type.lineHeight }]]
  ))),
  lineHeight: namespaced(tokens.type.leading),
  letterSpacing: namespaced(tokens.type.tracking),
  fontWeight: namespaced(tokens.type.weights),
  spacing: namespaced(spacing),
  maxWidth: namespaced(tokens.layout),
  borderRadius: namespaced(tokens.radii),
  boxShadow: namespaced(tokens.shadows),
  transitionDuration: namespaced(tokens.motion.duration),
  transitionTimingFunction: namespaced(tokens.motion.ease),
};

function plugin({ addBase }) {
  const variables = {};
  const addVariables = (prefix, values) => Object.entries(values).forEach(
    ([name, value]) => { variables[`--sh-${prefix}${name}`] = value; }
  );
  addVariables('', tokens.colors);
  addVariables('font-', Object.fromEntries(Object.entries(tokens.fonts.roles).map(
    ([name, font]) => [name, fontStack(font.family)]
  )));
  Object.entries(tokens.type.scale).forEach(([name, type]) => {
    variables[`--sh-type-${name}`] = type.size;
    variables[`--sh-type-leading-${name}`] = type.lineHeight;
  });
  addVariables('leading-', tokens.type.leading);
  addVariables('tracking-', tokens.type.tracking);
  addVariables('weight-', tokens.type.weights);
  addVariables('space-', tokens.spacing.scale);
  addVariables('space-', Object.fromEntries(Object.entries(tokens.spacing.semantic).map(
    ([name, scale]) => [name, `var(--sh-space-${scale})`]
  )));
  addVariables('layout-', tokens.layout);
  addVariables('breakpoint-', tokens.breakpoints);
  addVariables('radius-', tokens.radii);
  addVariables('shadow-', tokens.shadows);
  addVariables('motion-', tokens.motion.duration);
  variables['--sh-motion-ease'] = tokens.motion.ease.standard;
  addBase({ ':root': variables });

  Object.values(tokens.fonts.roles).forEach((font) => addBase({
    '@font-face': {
      fontFamily: font.family[0],
      fontStyle: 'normal',
      fontWeight: font.weight,
      fontDisplay: tokens.fonts.display,
      src: `url("${font.source}") format("woff2")`,
      unicodeRange: tokens.fonts.latinRange,
    },
  }));
}

module.exports = { theme, plugin, validateTokens };
