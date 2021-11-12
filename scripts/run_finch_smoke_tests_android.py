#!/usr/bin/env vpython3
# Copyright 2021 The Chromium Authors. All rights reserved.
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.

import argparse
import contextlib
import json
import logging
import os
import posixpath
import re
import shutil
import sys
import time

from collections import OrderedDict

SRC_DIR = os.path.abspath(
    os.path.join(os.path.dirname(__file__), os.pardir, os.pardir))
BLINK_TOOLS = os.path.join(
    SRC_DIR, 'third_party', 'blink', 'tools')
BUILD_ANDROID = os.path.join(SRC_DIR, 'build', 'android')
CATAPULT_DIR = os.path.join(SRC_DIR, 'third_party', 'catapult')
PYUTILS = os.path.join(CATAPULT_DIR, 'common', 'py_utils')
TEST_SEED_PATH = os.path.join(SRC_DIR, 'testing', 'scripts',
                              'variations_smoke_test_data',
                              'webview_test_seed')

if PYUTILS not in sys.path:
  sys.path.append(PYUTILS)

if BUILD_ANDROID not in sys.path:
  sys.path.append(BUILD_ANDROID)

if BLINK_TOOLS not in sys.path:
  sys.path.append(BLINK_TOOLS)

import common
import devil_chromium
import wpt_common

from blinkpy.web_tests.port.android import (
    ANDROID_WEBLAYER, ANDROID_WEBVIEW, CHROME_ANDROID)

from devil import devil_env
from devil.android import apk_helper
from devil.android import flag_changer
from devil.android import logcat_monitor
from devil.android.tools import script_common
from devil.android.tools import system_app
from devil.android.tools import webview_app
from devil.utils import logging_common

from pylib.local.emulator import avd
from py_utils.tempfile_ext import NamedTemporaryDirectory

from wpt_android_lib import add_emulator_args, get_device

_LOGCAT_FILTERS = [
  'chromium:v',
  'cr_*:v',
  'DEBUG:I',
  'StrictMode:D',
  'WebView*:v'
]
logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)
TEST_CASES = {}


class FinchTestCase(wpt_common.BaseWptScriptAdapter):


  def __init__(self, device):
    super(FinchTestCase, self).__init__()
    self._device = device
    self.parse_args()
    self.output_directory = os.path.join(SRC_DIR, 'out', self.options.target)
    self.mojo_js_directory = os.path.join(self.output_directory, 'gen')
    self.flags = flag_changer.FlagChanger(
        self._device, '%s-command-line' % self.product_name())
    self.browser_package_name = apk_helper.GetPackageName(
        self.options.browser_apk)
    self.flags.ReplaceFlags(self.browser_command_line_args())
    self.browser_activity_name = (self.options.browser_activity_name or
                                  self.default_browser_activity_name)
    self.log_mon = None

    if self.options.webview_provider_apk:
      self.webview_provider_package_name = (
          apk_helper.GetPackageName(self.options.webview_provider_apk))

  @classmethod
  def app_user_sub_dir(cls):
    """Returns sub directory within user directory"""
    return 'app_%s' % cls.product_name()

  @classmethod
  def product_name(cls):
    raise NotImplementedError

  @classmethod
  def wpt_product_name(cls):
    raise NotImplementedError

  @property
  def tests(self):
    return [
      'dom/collections/HTMLCollection-delete.html',
      'dom/collections/HTMLCollection-supported-property-names.html',
      'dom/collections/HTMLCollection-supported-property-indices.html',
    ]

  @property
  def default_browser_activity_name(self):
    raise NotImplementedError

  def __enter__(self):
    self._device.EnableRoot()
    self.log_mon = logcat_monitor.LogcatMonitor(
          self._device.adb,
          output_file=os.path.join(
              os.path.dirname(self.options.isolated_script_test_output),
              '%s_finch_smoke_tests_logcat.txt' % self.product_name()),
          filter_specs=_LOGCAT_FILTERS)
    self.log_mon.Start()
    return self

  def __exit__(self, exc_type, exc_val, exc_tb):
    self.flags.ReplaceFlags([])
    self.log_mon.Stop()

  @property
  def rest_args(self):
    rest_args = super(FinchTestCase, self).rest_args
    # Update the output directory to the default if it's not set.
    self.maybe_set_default_isolated_script_test_output()

    # Here we add all of the arguments required to run WPT tests on Android.
    rest_args.extend([
        os.path.join(SRC_DIR, 'third_party', 'wpt_tools', 'wpt', 'wpt')])

    # By default, WPT will treat unexpected passes as errors, so we disable
    # that to be consistent with Chromium CI.
    rest_args.extend(['--no-fail-on-unexpected-pass'])

    # vpython has packages needed by wpt, so force it to skip the setup
    rest_args.extend(['--venv=' + SRC_DIR, '--skip-venv-setup'])

    rest_args.extend(['run',
      self.wpt_product_name(),
      '--tests=' + wpt_common.EXTERNAL_WPT_TESTS_DIR,
      '--test-type=' + 'testharness',
      '--device-serial',
      self._device.serial,
      '--webdriver-binary',
      os.path.join('clang_x64', 'chromedriver'),
      '--symbols-path',
      self.output_directory,
      '--package-name',
      self.browser_package_name,
      '--keep-app-data-directory',
      '--no-pause-after-test',
      '--no-capture-stdio',
      '--no-manifest-download',
      '--enable-mojojs',
      '--mojojs-path=' + self.mojo_js_directory,
    ])

    for test in self.tests:
      rest_args.extend(['--include', test])

    if self.options.verbose >= 3:
      rest_args.extend(['--log-mach=-', '--log-mach-level=debug',
                        '--log-mach-verbose'])

    if self.options.verbose >= 4:
      rest_args.extend(['--webdriver-arg=--verbose',
                        '--webdriver-arg="--log-path=-"'])

    return rest_args

  @classmethod
  def add_extra_arguments(cls, parser):
    parser.add_argument('--test-case',
                        choices=TEST_CASES.keys(),
                        # TODO(rmhasan): Remove default values after
                        # adding arguments to test suites. Also make
                        # this argument required.
                        default='webview',
                        help='Name of test case')
    parser.add_argument('--finch-seed-path', default=TEST_SEED_PATH,
                        type=os.path.realpath,
                        help='Path to the finch seed')
    parser.add_argument('--browser-apk',
                        '--webview-shell-apk',
                        '--weblayer-shell-apk',
                        help='Path to the browser apk',
                        type=os.path.realpath,
                        required=True)
    parser.add_argument('--webview-provider-apk',
                        type=os.path.realpath,
                        help='Path to the WebView provider apk')
    parser.add_argument('--browser-activity-name',
                        action='store',
                        help='Browser activity name')
    parser.add_argument('--target',
                        action='store',
                        default='Release',
                        help='Build configuration')

    add_emulator_args(parser)
    script_common.AddDeviceArguments(parser)
    script_common.AddEnvironmentArguments(parser)
    logging_common.AddLoggingArguments(parser)

  @contextlib.contextmanager
  def _install_apks(self):
    yield

  @contextlib.contextmanager
  def install_apks(self):
    """Install apks for testing"""
    self._device.Uninstall(self.browser_package_name)
    self._device.Install(self.options.browser_apk, reinstall=True)
    yield

  def browser_command_line_args(self):
    # TODO(rmhasan): Add browser command line arguments
    # for weblayer and chrome
    return []

  def run_tests(self, test_run_variation, results_dict):
    """Run browser test on test device

    Args:
      test_suffix: Suffix for log output

    Returns:
      True if browser did not crash or False if the browser crashed
    """
    self.layout_test_results_subdir = ('%s_smoke_test_artifacts' %
                                       test_run_variation)
    ret = super(FinchTestCase, self).run_test()
    self.stop_browser()

    with open(self.wpt_output, 'r') as curr_test_results:
      curr_results_dict = json.loads(curr_test_results.read())
      results_dict['tests'][test_run_variation] = curr_results_dict['tests']

      for result, count in curr_results_dict['num_failures_by_type'].items():
        results_dict['num_failures_by_type'].setdefault(result, 0)
        results_dict['num_failures_by_type'][result] += count

    return ret

  def stop_browser(self):
    logger.info('Stopping package %s', self.browser_package_name)
    self._device.ForceStop(self.browser_package_name)
    if self.options.webview_provider_apk:
      logger.info('Stopping package %s', self.webview_provider_package_name)
      self._device.ForceStop(
          self.webview_provider_package_name)

  def start_browser(self):
    full_activity_name = '%s/%s' % (self.browser_package_name,
                                    self.browser_activity_name)
    logger.info('Starting activity %s', full_activity_name)

    self._device.RunShellCommand([
          'am',
          'start',
          '-W',
          '-n',
          full_activity_name,
          '-d',
          'data:,'])
    logger.info('Waiting 10 seconds')
    time.sleep(10)

  def _wait_for_local_state_file(self, local_state_file):
    """Wait for local state file to be generated"""
    max_wait_time_secs = 120
    delta_secs = 10
    total_wait_time_secs = 0

    self.start_browser()

    while total_wait_time_secs < max_wait_time_secs:
      if self._device.PathExists(local_state_file):
        logger.info('Local state file generated')
        self.stop_browser()
        return

      logger.info('Waiting %d seconds for the local state file to generate',
                  delta_secs)
      time.sleep(delta_secs)
      total_wait_time_secs += delta_secs

    raise Exception('Timed out waiting for the '
                    'local state file to be generated')

  def install_seed(self):
    """Install finch seed for testing

    Returns:
      None
    """
    app_data_dir = posixpath.join(
        self._device.GetApplicationDataDirectory(self.browser_package_name),
        self.app_user_sub_dir())
    device_local_state_file = posixpath.join(app_data_dir, 'Local State')

    self._wait_for_local_state_file(device_local_state_file)

    with NamedTemporaryDirectory() as tmp_dir:
      tmp_ls_path = os.path.join(tmp_dir, 'local_state.json')
      self._device.adb.Pull(device_local_state_file, tmp_ls_path)

      with open(tmp_ls_path, 'r') as local_state_content, \
          open(self.options.finch_seed_path, 'r') as test_seed_content:
        local_state_json = json.loads(local_state_content.read())
        test_seed_json = json.loads(test_seed_content.read())

        # Copy over the seed data and signature
        local_state_json['variations_compressed_seed'] = (
            test_seed_json['variations_compressed_seed'])
        local_state_json['variations_seed_signature'] = (
            test_seed_json['variations_seed_signature'])

        with open(os.path.join(tmp_dir, 'new_local_state.json'),
                  'w') as new_local_state:
          new_local_state.write(json.dumps(local_state_json))

        self._device.adb.Push(new_local_state.name, device_local_state_file)
        user_id = self._device.GetUidForPackage(self.browser_package_name)
        logger.info('Setting owner of Local State file to %r', user_id)
        self._device.RunShellCommand(
            ['chown', user_id, device_local_state_file], as_root=True)


class ChromeFinchTestCase(FinchTestCase):

  @classmethod
  def product_name(cls):
    """Returns name of product being tested"""
    return 'chrome'

  @classmethod
  def wpt_product_name(cls):
    return CHROME_ANDROID

  @property
  def default_browser_activity_name(self):
    return 'org.chromium.chrome.browser.ChromeTabbedActivity'


class WebViewFinchTestCase(FinchTestCase):

  @classmethod
  def product_name(cls):
    """Returns name of product being tested"""
    return 'webview'

  @classmethod
  def wpt_product_name(cls):
    return ANDROID_WEBVIEW

  @property
  def default_browser_activity_name(self):
    return 'org.chromium.webview_shell.WebPlatformTestsActivity'

  def browser_command_line_args(self):
    return ['--webview-verbose-logging']

  @contextlib.contextmanager
  def install_apks(self):
    """Install apks for testing"""
    with super(WebViewFinchTestCase, self).install_apks(), \
      webview_app.UseWebViewProvider(self._device,
                                     self.options.webview_provider_apk):
      yield

  def install_seed(self):
    """Install finch seed for testing

    Returns:
      None
    """
    app_data_dir = posixpath.join(
        self._device.GetApplicationDataDirectory(self.browser_package_name),
        self.app_user_sub_dir())
    self._device.RunShellCommand(['mkdir', '-p', app_data_dir],
                                run_as=self.browser_package_name)

    seed_path = posixpath.join(app_data_dir, 'variations_seed')
    seed_new_path = posixpath.join(app_data_dir, 'variations_seed_new')
    seed_stamp = posixpath.join(app_data_dir, 'variations_stamp')

    self._device.adb.Push(self.options.finch_seed_path, seed_path)
    self._device.adb.Push(self.options.finch_seed_path, seed_new_path)
    self._device.RunShellCommand(
        ['touch', seed_stamp], check_return=True,
        run_as=self.browser_package_name)

    # We need to make the WebView shell package an owner of the seeds,
    # see crbug.com/1191169#c19
    user_id = self._device.GetUidForPackage(self.browser_package_name)
    logger.info('Setting owner of seed files to %r', user_id)
    self._device.RunShellCommand(['chown', user_id, seed_path], as_root=True)
    self._device.RunShellCommand(
        ['chown', user_id, seed_new_path], as_root=True)


class WebLayerFinchTestCase(FinchTestCase):

  @classmethod
  def product_name(cls):
    """Returns name of product being tested"""
    return 'weblayer'

  @classmethod
  def wpt_product_name(cls):
    return ANDROID_WEBLAYER

  @property
  def default_browser_activity_name(self):
    return 'org.chromium.weblayer.shell.WebLayerShellActivity'

  @contextlib.contextmanager
  def install_apks(self):
    """Install apks for testing"""
    with super(WebLayerFinchTestCase, self).install_apks(), \
      webview_app.UseWebViewProvider(self._device,
                                     self.options.webview_provider_apk):
      yield


def main(args):
  TEST_CASES.update(
      {p.product_name(): p
       for p in [ChromeFinchTestCase, WebViewFinchTestCase,
                 WebLayerFinchTestCase]})

  parser = argparse.ArgumentParser()

  FinchTestCase.add_extra_arguments(parser)
  parser.add_argument(
        '--isolated-script-test-output', type=str,
        required=False,
        help='path to write test results JSON object to')
  options, _ = parser.parse_known_args(args)

  with get_device(options) as device, \
      TEST_CASES[options.test_case](device) as test_case, \
      test_case.install_apks():

    devil_chromium.Initialize(adb_path=options.adb_path)
    logging_common.InitializeLogging(options)

    # TODO(rmhasan): Best practice in Chromium is to allow users to provide
    # their own adb binary to avoid adb server restarts. We should add a new
    # command line argument to wptrunner so that users can pass the path to
    # their adb binary.
    platform_tools_path = os.path.dirname(devil_env.config.FetchPath('adb'))
    os.environ['PATH'] = os.pathsep.join([platform_tools_path] +
                                          os.environ['PATH'].split(os.pathsep))

    device.RunShellCommand(
        ['pm', 'clear', test_case.browser_package_name],
        check_return=True)

    test_results_dict = OrderedDict({'version': 3, 'interrupted': False,
                                     'num_failures_by_type': {}, 'tests': {}})

    ret = test_case.run_tests('without_finch_seed', test_results_dict)
    test_case.install_seed()
    ret |= test_case.run_tests('with_finch_seed', test_results_dict)

    test_results_dict['seconds_since_epoch'] = int(time.time())
    test_results_dict['path_delimiter'] = '/'

    with open(options.isolated_script_test_output, 'w') as json_out:
      json_out.write(json.dumps(test_results_dict, indent=4))

  # Return zero exit code if tests pass
  return ret


def main_compile_targets(args):
  json.dump([], args.output)


if __name__ == '__main__':
  if 'compile_targets' in sys.argv:
    funcs = {
      'run': None,
      'compile_targets': main_compile_targets,
    }
    sys.exit(common.run_script(sys.argv[1:], funcs))
  sys.exit(main(sys.argv[1:]))
