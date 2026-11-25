// This file is part of Moodle - https://moodle.org/
//
// Moodle is free software: you can redistribute it and/or modify
// it under the terms of the GNU General Public License as published by
// the Free Software Foundation, either version 3 of the License, or
// (at your option) any later version.
//
// Moodle is distributed in the hope that it will be useful,
// but WITHOUT ANY WARRANTY; without even the implied warranty of
// MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
// GNU General Public License for more details.
//
// You should have received a copy of the GNU General Public License

// Tests for local_llmchat/links: the ref cache, replay batching and chunked requests.
// Run: node --test moodle/local_llmchat/tests/js/*.test.mjs (Node 22.15+)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {registerHooks} from 'node:module';

const amdSrc = new URL('../../amd/src/', import.meta.url);
registerHooks({
    resolve(specifier, context, next) {
        if (specifier === 'local_llmchat/render') {
            // Rendering needs Moodle's templates; these tests only look at what is computed.
            return {url: 'data:text/javascript,export const renderInto = async () => {};', shortCircuit: true};
        }
        const name = specifier.match(/^local_llmchat\/(.+)$/)?.[1];
        if (!name) {
            return next(specifier, context);
        }
        return {url: new URL(`${name}.js`, amdSrc).href, shortCircuit: true};
    },
});

const {LinkResolver} = await import('../../amd/src/links.js');

/**
 * A fake web service: answers every ref of a chunk with a url built from its id.
 *
 * @returns {Object} {fetch, calls} where calls records the refs of every chunk asked for
 */
const fakeService = () => {
    const calls = [];
    const fetch = (chunks) => chunks.map(async(refs) => {
        calls.push(refs);
        return refs.map((ref) => ({...ref, url: `https://moodle.test/${ref.kind}/${ref.id}`}));
    });
    return {fetch, calls};
};

test('a ref is resolved to its url and cached', async() => {
    const {fetch, calls} = fakeService();
    const links = new LinkResolver(fetch);
    const first = await links.resolveRefs([{kind: 'cm', id: 1}, {kind: 'folder', id: 2}]);
    assert.deepEqual(first, {'cm:1': 'https://moodle.test/cm/1', 'folder:2': 'https://moodle.test/folder/2'});
    await links.resolveRefs([{kind: 'cm', id: 1}]);
    assert.equal(calls.length, 1, 'the second call is answered from the cache');
});

test('more than 100 refs go out in chunks of 100', async() => {
    const {fetch, calls} = fakeService();
    const links = new LinkResolver(fetch);
    const refs = Array.from({length: 250}, (_, i) => ({kind: 'cm', id: i}));
    const out = await links.resolveRefs(refs);
    assert.deepEqual(calls.map((c) => c.length), [100, 100, 50]);
    assert.equal(Object.keys(out).length, 250);
});

test('a failed request resolves to an empty url and is asked again next time', async() => {
    let fail = true;
    const asked = [];
    const links = new LinkResolver((chunks) => chunks.map(async(refs) => {
        asked.push(refs.length);
        if (fail) {
            throw new Error('offline');
        }
        return refs.map((ref) => ({...ref, url: 'https://moodle.test/ok'}));
    }));
    assert.deepEqual(await links.resolveRefs([{kind: 'cm', id: 1}]), {'cm:1': ''});
    fail = false;
    assert.deepEqual(await links.resolveRefs([{kind: 'cm', id: 1}]), {'cm:1': 'https://moodle.test/ok'});
    assert.deepEqual(asked, [1, 1]);
});

test('refs of a replaying history are held back until flushRefs and asked for together', async() => {
    const {fetch, calls} = fakeService();
    const links = new LinkResolver(fetch);
    links.batchRefs();
    const a = links.resolveRefs([{kind: 'cm', id: 1}]);
    const b = links.resolveRefs([{kind: 'cm', id: 2}]);
    assert.equal(calls.length, 0, 'nothing is requested while the history replays');
    links.flushRefs();
    assert.deepEqual(await a, {'cm:1': 'https://moodle.test/cm/1'});
    assert.deepEqual(await b, {'cm:2': 'https://moodle.test/cm/2'});
    assert.equal(calls.length, 1);
    assert.equal(calls[0].length, 2);
});

test('clear forgets every resolved link', async() => {
    const {fetch, calls} = fakeService();
    const links = new LinkResolver(fetch);
    await links.resolveRefs([{kind: 'cm', id: 1}]);
    links.clear();
    await links.resolveRefs([{kind: 'cm', id: 1}]);
    assert.equal(calls.length, 2);
});
