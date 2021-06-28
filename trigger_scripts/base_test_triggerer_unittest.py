#!/usr/bin/env vpython
# Copyright 2020 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
"""Tests for base_device_trigger.py."""

import argparse
import json
import unittest

import mock

from pyfakefs import fake_filesystem_unittest

import base_test_triggerer


class UnitTest(fake_filesystem_unittest.TestCase):
    def setUp(self):
        self.setUpPyfakefs()

    def test_convert_to_go_swarming_args(self):
        args = [
            '--swarming', 'x.apphost.com', '--dimension', 'pool', 'ci',
            '--dimension', 'os', 'linux', '-env', 'FOO=foo', '--hello',
            '-cipd-package', 'path:name=123', '--scalar', '42',
            '--enable-resultdb'
        ]
        go_args = base_test_triggerer._convert_to_go_swarming_args(args)
        expected = [
            '--server', 'x.apphost.com', '--dimension', 'pool=ci',
            '--dimension', 'os=linux', '-env', 'FOO=foo', '--hello',
            '-cipd-package', 'path:name=123', '--scalar', '42',
            '--enable-resultdb'
        ]
        self.assertEquals(go_args, expected)

    def test_convert_to_go_swarming_args_failed(self):
        invalid_args = [
            # expected format: --dimension key value
            ([
                '--dimension',
                'key',
            ], IndexError),
        ]
        for args, ex in invalid_args:
            self.assertRaises(ex,
                              base_test_triggerer._convert_to_go_swarming_args,
                              args)

    def test_trigger_tasks(self):
        parser = base_test_triggerer.BaseTestTriggerer.setup_parser_contract(
            argparse.ArgumentParser())
        dump_json = 'dump.json'
        args, remaining = parser.parse_known_args([
            '--multiple-dimension-script-verbose', 'True', 'trigger',
            '--shards', '1', '--dump-json', dump_json
        ])
        triggerer = base_test_triggerer.BaseTestTriggerer()

        def mock_subprocess_call(args):
            # write json file generated by 'swarming trigger' command.
            json_path = args[args.index('--dump-json') + 1]
            with open(json_path, 'w') as f:
                f.write(
                    json.dumps({
                        'tasks': [{
                            'request': {
                                'task_id': 'f0',
                            },
                            'task_result': {
                                'resultdb_info': {
                                    'invocation': 'task-f0',
                                },
                            },
                        }],
                    }))

        # make some not important functions nop.
        with mock.patch.object(triggerer,
                               'parse_bot_configs'), mock.patch.object(
                                   triggerer, '_bot_configs',
                                   []), mock.patch.object(
                                       triggerer,
                                       'select_config_indices',
                                       return_value=[(0, 0)]), mock.patch(
                                           'subprocess.call',
                                           side_effect=mock_subprocess_call):
            triggerer.trigger_tasks(args, remaining)

        with open(dump_json) as f:
            self.assertEqual(
                json.load(f), {
                    u'tasks': {
                        u'f0:0:1': {
                            u'shard_index': 0,
                            u'task_id': u'f0',
                            u'invocation': u'task-f0',
                        }
                    }
                })


if __name__ == '__main__':
    unittest.main()
