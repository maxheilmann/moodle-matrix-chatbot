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

// Tests for local_llmchat/util.
// Run: node --test moodle/local_llmchat/tests/js/*.test.mjs (Node 22.15+)
import {test} from 'node:test';
import assert from 'node:assert/strict';
import {fill, formatNumber, formatTime, refKey} from '../../amd/src/util.js';

test('fill replaces {$a} and {$a->x}', () => {
    assert.equal(fill('Error: {$a}', 'boom'), 'Error: boom');
    assert.equal(fill('{$a->used} of {$a->limit}', {used: 3, limit: 10}), '3 of 10');
});

test('fill drops placeholders without a value', () => {
    assert.equal(fill('a {$a->missing} b', {used: 1}), 'a  b');
    assert.equal(fill('a {$a} b', undefined), 'a  b');
    assert.equal(fill('a {$a} b', null), 'a  b');
});

test('refKey joins kind and id', () => {
    assert.equal(refKey({kind: 'cm', id: 7}), 'cm:7');
});

test('formatTime reads seconds and ISO strings and leaves nonsense alone', () => {
    assert.equal(formatTime(''), '');
    assert.equal(formatTime(null), '');
    assert.equal(formatTime(undefined), '');
    assert.equal(formatTime(1_700_000_000), new Date(1_700_000_000 * 1000).toLocaleString());
    assert.equal(formatTime('2026-01-02T03:04:05Z'), new Date('2026-01-02T03:04:05Z').toLocaleString());
    assert.equal(formatTime('not a date'), 'not a date');
});

test('formatNumber formats numbers and shows a dash for nothing', () => {
    assert.equal(formatNumber(12), (12).toLocaleString());
    assert.equal(formatNumber(null), '–');
    assert.equal(formatNumber(undefined), '–');
    assert.equal(formatNumber('n/a'), 'n/a');
});
