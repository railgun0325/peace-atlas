import test from 'node:test';
import assert from 'node:assert/strict';
import { graphNodeContour } from '../interactive_graph.js';

test('organization outer shape is a rounded square contained by the unchanged physics radius', () => {
  for (const radius of [5, 6.5, 8, 12, 17]) {
    const { tag, attrs } = graphNodeContour('organization', radius);
    assert.equal(tag, 'rect');
    assert.equal(attrs.width, attrs.height);
    assert.equal(attrs.x, -attrs.width / 2);
    assert.equal(attrs.y, -attrs.height / 2);
    assert.ok(Math.hypot(attrs.x, attrs.y) <= radius + 1e-12);
    assert.ok(attrs.rx > 0 && attrs.rx < attrs.width / 2);
  }
});

test('individual outer contour stays circular at the exact solver radius', () => {
  for (const radius of [5, 6.5, 8, 12, 17]) {
    assert.deepEqual(graphNodeContour('individual', radius), { tag: 'circle', attrs: { r: radius } });
  }
});

test('legacy unknown, missing and unsupported types share the personal circular contour', () => {
  for (const type of ['unknown', undefined, 'organization-name-only']) {
    for (const radius of [5, 8, 12, 17]) {
      assert.deepEqual(graphNodeContour(type, radius), { tag: 'circle', attrs: { r: radius } });
    }
  }
});

test('selection halo preserves each type contour instead of introducing a common circular outline', () => {
  for (const type of ['organization', 'individual', 'unknown']) {
    const inner = graphNodeContour(type, 8), halo = graphNodeContour(type, 13);
    assert.equal(inner.tag, halo.tag);
    assert.notDeepEqual(inner.attrs, halo.attrs);
  }
});
