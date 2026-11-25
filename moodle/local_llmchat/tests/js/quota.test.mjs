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

// Tests for local_llmchat/quota: what the token budget shows for a quota frame.
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

const {quotaView} = await import('../../amd/src/quota.js');

const STR = {
    'quota:today': 'Today',
    'quota:thismonth': 'This month',
    'quota:remaining': '{$a->used} of {$a->limit} used today',
    'quota:remainingmonth': '{$a->used} of {$a->limit} used this month',
    'quota:usedof': '{$a->used} / {$a->limit}',
    'quota:exemptuser': 'No limit',
};

test('the ring shows the fullest window', () => {
    const view = quotaView({'used_today': 10, 'limit_today': 100, 'used_month': 50, 'limit_month': 100}, STR);
    assert.deepEqual(view.rows.map((r) => [r.label, r.percent]), [['Today', 10], ['This month', 50]]);
    assert.equal(view.percent, 50);
    assert.equal(view.label, '50%');
    assert.equal(view.state, '');
    assert.equal(view.visible, true);
    assert.equal(view.tooltip, '10 of 100 used today · 50 of 100 used this month');
});

test('80 percent warns and 100 percent is danger, capped at 100', () => {
    assert.equal(quotaView({'used_today': 80, 'limit_today': 100}, STR).state, 'warning');
    const full = quotaView({'used_today': 250, 'limit_today': 100}, STR);
    assert.equal(full.state, 'danger');
    assert.equal(full.percent, 100);
    assert.equal(full.rows[0].danger, true);
    assert.equal(full.rows[0].warning, false);
});

test('a window without a limit is left out and nothing is shown without any', () => {
    const month = quotaView({'used_today': 5, 'limit_today': 0, 'used_month': 5, 'limit_month': 20}, STR);
    assert.deepEqual(month.rows.map((r) => r.label), ['This month']);
    const none = quotaView({'used_today': 5}, STR);
    assert.deepEqual(none.rows, []);
    assert.equal(none.visible, false);
    assert.equal(none.percent, 0);
});

test('an exempt user sees the infinity sign and no reset time', () => {
    const view = quotaView({exempt: true, 'resets_at': 1_700_000_000}, STR);
    assert.equal(view.label, '∞');
    assert.equal(view.percent, 0);
    assert.equal(view.tooltip, 'No limit');
    assert.equal(view.hasresets, false);
    assert.equal(view.visible, true);
});

test('the reset time is shown when there is a limit', () => {
    const view = quotaView({'used_today': 1, 'limit_today': 10, 'resets_at': 1_700_000_000}, STR);
    assert.equal(view.hasresets, true);
    assert.equal(view.resets, new Date(1_700_000_000 * 1000).toLocaleString());
});
