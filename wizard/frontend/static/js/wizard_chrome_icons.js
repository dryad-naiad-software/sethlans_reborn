// SPDX-FileCopyrightText: 2025 Dryad and Naiad Software LLC
//
// SPDX-License-Identifier: GPL-2.0-or-later

// Inline SVG glyphs for the wizard stepper (issue #179).
//
// Using inline SVG avoids vendoring a separate icon font (Bootstrap
// Icons would add a non-trivial CSS+font payload for ~8 glyphs we
// actually use). Each SVG is sized to 18px and inherits
// `currentColor` so the stepper's brand-fill / muted-gray styles
// apply uniformly.
//
// Glyphs derived from Bootstrap Icons 1.11.x (MIT). The original
// path data is preserved verbatim — only the wrapping <svg> element
// was re-emitted with our sizing/colour conventions.

export const ICON_TOPOLOGY =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M8 0a2 2 0 0 1 2 2v3a2 2 0 0 1-1 1.732V8h3a2 2 0 0 1 2 2v.268A2 '
  + '2 0 1 1 13 14a2 2 0 0 1-1-3.732V10h-3v.268A2 2 0 1 1 7 14a2 2 0 0 1-1-3.732V10H3'
  + 'v.268A2 2 0 1 1 2 10v-.268A2 2 0 0 1 3 8h3V6.732A2 2 0 0 1 6 5V2a2 2 0 0 1 2-2"/>'
  + '</svg>';

export const ICON_NETWORK =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M0 0h6v6H0zm10 0h6v6h-6zM0 10h6v6H0zm10 0h6v6h-6zM2 2v2h2V2zm10 0v2'
  + 'h2V2zM2 12v2h2v-2zm10 0v2h2v-2z"/></svg>';

export const ICON_DATABASE =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M4.318 2.687C5.234 2.271 6.536 2 8 2s2.766.27 3.682.687C12.644 3.125 '
  + '13 3.627 13 4c0 .374-.356.875-1.318 1.313C10.766 5.729 9.464 6 8 6s-2.766-.27-3'
  + '.682-.687C3.356 4.875 3 4.373 3 4c0-.374.356-.875 1.318-1.313M13 5.698V7c0 .37'
  + '4-.356.875-1.318 1.313C10.766 8.729 9.464 9 8 9s-2.766-.27-3.682-.687C3.356 7.'
  + '875 3 7.373 3 7V5.698c.271.202.58.378.904.525C4.978 6.711 6.427 7 8 7s3.022-.2'
  + '9 4.096-.777A5 5 0 0 0 13 5.698M14 4c0-1.007-.875-1.755-1.904-2.223C11.022 1.2'
  + '89 9.573 1 8 1s-3.022.289-4.096.777C2.875 2.245 2 2.993 2 4v9c0 1.007.875 1.75'
  + '5 1.904 2.223C4.978 15.71 6.427 16 8 16s3.022-.289 4.096-.777C13.125 14.755 14'
  + ' 14.007 14 13zm-1 4.698V10c0 .374-.356.875-1.318 1.313C10.766 11.729 9.464 12 '
  + '8 12s-2.766-.27-3.682-.687C3.356 10.875 3 10.373 3 10V8.698c.271.202.58.378.90'
  + '4.525C4.978 9.71 6.427 10 8 10s3.022-.29 4.096-.777A5 5 0 0 0 13 8.698m0 3V13c'
  + '0 .374-.356.875-1.318 1.313C10.766 14.729 9.464 15 8 15s-2.766-.27-3.682-.687C'
  + '3.356 13.875 3 13.373 3 13v-1.302c.271.202.58.378.904.525C4.978 12.71 6.427 13'
  + ' 8 13s3.022-.29 4.096-.777A5 5 0 0 0 13 11.698"/></svg>';

export const ICON_PERSON =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M8 8a3 3 0 1 0 0-6 3 3 0 0 0 0 6m2-3a2 2 0 1 1-4 0 2 2 0 0 1 4 0m4 8c'
  + '0 1-1 1-1 1H3s-1 0-1-1 1-4 6-4 6 3 6 4m-1-.004c-.001-.246-.154-.986-.832-1.66'
  + '4C11.516 10.68 10.289 10 8 10s-3.516.68-4.168 1.332c-.678.678-.83 1.418-.832 '
  + '1.664z"/></svg>';

export const ICON_KEY =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M0 8a4 4 0 0 1 7.465-2H14a.5.5 0 0 1 .354.146l1.5 1.5a.5.5 0 0 1 0 .'
  + '708l-1.5 1.5a.5.5 0 0 1-.708 0L13 9.207l-.646.647a.5.5 0 0 1-.708 0L11 9.207l'
  + '-.646.647a.5.5 0 0 1-.708 0L9 9.207l-.646.647A.5.5 0 0 1 8 10h-.535A4 4 0 0 1'
  + ' 0 8m4-3a3 3 0 1 0 2.712 4.285A.5.5 0 0 1 7.163 9h.63l.853-.854a.5.5 0 0 1 .7'
  + '08 0l.646.647.646-.647a.5.5 0 0 1 .708 0l.646.647.646-.647a.5.5 0 0 1 .708 0l'
  + '.646.647.793-.793-1-1h-6.63a.5.5 0 0 1-.451-.285A3 3 0 0 0 4 5"/>'
  + '<path d="M4 8a1 1 0 1 1-2 0 1 1 0 0 1 2 0"/></svg>';

export const ICON_FILM =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M0 1a1 1 0 0 1 1-1h14a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H1a1 1 0 0 1-1-1'
  + 'zm4 0v6h8V1zm8 8H4v6h8zM1 1v2h2V1zm2 3H1v2h2zM1 7v2h2V7zm2 3H1v2h2zm-2 3v2h2v'
  + '-2zM15 1h-2v2h2zm-2 3v2h2V4zm2 3h-2v2h2zm-2 3v2h2v-2zm2 3h-2v2h2z"/></svg>';

export const ICON_CHECK =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M10.97 4.97a.75.75 0 0 1 1.07 1.05l-3.99 4.99a.75.75 0 0 1-1.08.02L'
  + '4.324 8.384a.75.75 0 1 1 1.06-1.06l2.094 2.093 3.473-4.425z"/></svg>';

export const ICON_FLAG =
  '<svg xmlns="http://www.w3.org/2000/svg" width="18" height="18" '
  + 'viewBox="0 0 16 16" fill="currentColor" aria-hidden="true">'
  + '<path d="M14.778.085A.5.5 0 0 1 15 .5V8a.5.5 0 0 1-.314.464L14.5 8l.186.464-.'
  + '003.001-.006.003-.023.009a12 12 0 0 1-.397.15c-.264.095-.631.223-1.047.35-.8'
  + '14.252-1.86.523-2.71.523-.853 0-1.397-.272-1.917-.532l-.062-.031c-.524-.262-'
  + '1.04-.518-1.768-.621-.729-.103-1.692.044-3.265.485l-.244.069v6.65a.5.5 0 1 1'
  + '-1 0V.5a.5.5 0 0 1 .582-.493q.234.038.474.083C3.494.394 4.448.5 5.5.5c.747 0'
  + ' 1.45-.097 2.013-.205C8.04.207 8.494.103 8.93 0c.728-.171 1.535-.171 2.262 0'
  + 'q.418.103.836.21A11 11 0 0 0 13.5.5q.5 0 1-.118.234-.044.466-.083a.5.5 0 0 1'
  + '.404.176z"/></svg>';
