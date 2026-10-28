// SPDX-License-Identifier: AGPL-3.0-only

/** printhub.edits.snap_crop: clamp to the image, then move the top-left corner down to an iMCU boundary. */
export function snapCrop(crop, [width, height], [mw, mh]) {
  const clamp = (v, max) => Math.min(Math.max(v, 0), max);
  const left = clamp(crop.x, width), top = clamp(crop.y, height);
  const right = clamp(crop.x + crop.w, width), bottom = clamp(crop.y + crop.h, height);
  if (right <= left || bottom <= top) throw new Error("crop lies outside the image");
  const x = Math.floor(left / mw) * mw, y = Math.floor(top / mh) * mh;
  return { x, y, w: right - x, h: bottom - y };
}

// printhub.edits._trim: jpegtran -trim keeps a dimension smaller than one iMCU whole
const trim = (n, unit) => Math.floor(n / unit) * unit || n;

/**
 * Map a crop in a page frame of `size` (page_info width/height, with its `mcu`) through a ±90° turn.
 * jpegtran -trim only ever drops partial iMCUs at the frame's right and bottom edges, so the part every
 * rotation keeps is the top-left trim(width) x trim(height) block; the crop turns with that block.
 * Returns null when the crop lies only in a strip the turn trims away.
 */
export function rotateCrop(crop, [width, height], delta, [mw, mh]) {
  const keepW = trim(width, mw), keepH = trim(height, mh);
  // +90 (clockwise) maps (x, y) to (keepH - y, x); -90 maps it to (y, keepW - x).
  const r = delta === 90
    ? { left: keepH - crop.y - crop.h, right: keepH - crop.y, top: crop.x, bottom: crop.x + crop.w }
    : { left: crop.y, right: crop.y + crop.h, top: keepW - crop.x - crop.w, bottom: keepW - crop.x };
  const left = Math.max(r.left, 0), top = Math.max(r.top, 0);
  if (r.right <= left || r.bottom <= top) return null;
  return { x: left, y: top, w: r.right - left, h: r.bottom - top };
}
