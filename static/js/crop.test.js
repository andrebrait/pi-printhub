// SPDX-License-Identifier: AGPL-3.0-only
import test from "node:test";
import assert from "node:assert/strict";
import { rotateCrop, snapCrop } from "./crop.js";

// tests/test_edits.py::test_snap_crop_boundaries: Crop(x, y, 40, 30) on 100x60; offset -> snapped origin per iMCU.
const SNAPPED = {
  16: { 0: 0, 7: 0, 8: 0, 15: 0, 16: 16, 17: 16 },
  8: { 0: 0, 7: 0, 8: 8, 15: 8, 16: 16, 17: 16 },
};

for (const m of [16, 8]) {
  for (const x of [0, 7, 8, 15, 16, 17]) {
    for (const y of [0, 7, 8, 15, 16, 17]) {
      test(`snapCrop boundary x=${x} y=${y} mcu=${m}`, () => {
        const sx = SNAPPED[m][x], sy = SNAPPED[m][y];
        assert.deepEqual(snapCrop({ x, y, w: 40, h: 30 }, [100, 60], [m, m]), { x: sx, y: sy, w: x + 40 - sx, h: y + 30 - sy });
      });
    }
  }
}

test("snapCrop clamps to the image (test_snap_crop_clamps)", () => {
  assert.deepEqual(snapCrop({ x: 50, y: 40, w: 100, h: 100 }, [100, 60], [16, 16]), { x: 48, y: 32, w: 52, h: 28 });
  assert.deepEqual(snapCrop({ x: -5, y: -3, w: 30, h: 20 }, [100, 60], [16, 16]), { x: 0, y: 0, w: 25, h: 17 });
  assert.deepEqual(snapCrop({ x: 0, y: 0, w: 48, h: 100 }, [48, 100], [16, 16]), { x: 0, y: 0, w: 48, h: 100 });
});

test("snapCrop rejects a crop outside the image", () => {
  assert.throws(() => snapCrop({ x: 100, y: 0, w: 10, h: 10 }, [100, 60], [16, 16]));
});

const inside = (c, [w, h]) => c.x >= 0 && c.y >= 0 && c.w > 0 && c.h > 0 && c.x + c.w <= w && c.y + c.h <= h;

test("rotateCrop by +90 four times returns the original crop", () => {
  const crop = { x: 10, y: 5, w: 30, h: 20 };
  let c = crop;
  for (const size of [[96, 48], [48, 96], [96, 48], [48, 96]]) c = rotateCrop(c, size, 90, [16, 16]);
  assert.deepEqual(c, crop);
});

// rotated_size(100, 60, r, (16, 16)) for r = 0, 90, 180, 270 (test_rotated_size_100x60_420 and jpegtran).
const FRAMES = [[100, 60], [48, 100], [96, 48], [60, 96]];

test("rotateCrop round trips through trimmed frames", () => {
  const crop = { x: 10, y: 5, w: 30, h: 20 };
  let c = crop;
  for (const size of FRAMES) c = rotateCrop(c, size, 90, [16, 16]);
  assert.deepEqual(c, crop);
  for (const size of [FRAMES[0], FRAMES[3], FRAMES[2], FRAMES[1]]) c = rotateCrop(c, size, -90, [16, 16]);
  assert.deepEqual(c, crop);
});

test("rotateCrop keeps the crop inside the new size after a trim", () => {
  const full = { x: 0, y: 0, w: 100, h: 60 };
  const cw = rotateCrop(full, FRAMES[0], 90, [16, 16]);
  assert.deepEqual(cw, { x: 0, y: 0, w: 48, h: 100 });
  assert.ok(inside(cw, FRAMES[1]));
  const ccw = rotateCrop(full, FRAMES[0], -90, [16, 16]);
  assert.deepEqual(ccw, { x: 0, y: 0, w: 60, h: 96 });
  assert.ok(inside(ccw, FRAMES[3]));
});

test("rotateCrop drops a crop that lies only in the trimmed strip", () => {
  assert.equal(rotateCrop({ x: 0, y: 50, w: 100, h: 10 }, FRAMES[0], 90, [16, 16]), null);
});
