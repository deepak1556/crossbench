# Copyright 2024 The Chromium Authors
# Use of this source code is governed by a BSD-style license that can be
# found in the LICENSE file.
from __future__ import annotations

import contextlib
import datetime as dt
import pathlib
import unittest
from typing import TYPE_CHECKING
from unittest import mock

from typing_extensions import override

from crossbench.action_runner.action.click import ClickAction
from crossbench.action_runner.action.position import PositionConfig
from crossbench.action_runner.action.scroll import ScrollAction
from crossbench.action_runner.action.swipe import SwipeAction
from crossbench.action_runner.action.text_input import TextInputAction
from crossbench.action_runner.android_input_action_runner import \
    AndroidInputActionRunner, ViewportInfo
from crossbench.action_runner.base import InputSourceNotImplementedError
from crossbench.action_runner.display_rectangle import DisplayRectangle
from crossbench.action_runner.element_not_found_error import \
    ElementNotFoundError
from crossbench.benchmarks.loading.input_source import InputSource
from crossbench.benchmarks.loading.point import Point
from crossbench.browsers.settings import Settings
from crossbench.flags.base import Flags
from crossbench.runner.groups.session import BrowserSessionRunGroup
from tests import test_helper
from tests.crossbench.action_runner.action_runner_test_case import \
    ActionRunnerTestCase
from tests.crossbench.mock_browser import JsInvocation, MockChromeAndroidStable
from tests.crossbench.mock_helper import AndroidAdbMockPlatform, \
    LinuxMockPlatform, MockAdb
from tests.crossbench.runner.helper import MockRun, MockRunner

if TYPE_CHECKING:
  from crossbench.action_runner.action.action import Action


class ViewportInfoTestCase(unittest.TestCase):

  def test_calculate_coordinates_no_element_still_returns_chrome_window(self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        window_inner_height=100,
        window_inner_width=100)

    self.assertTrue(config.chrome_window)
    self.assertFalse(config.element_rect())
    self.assertFalse(config.element_center())

  def test_calculate_coordinates_top_system_border_accounted_for(self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        window_inner_height=90,
        window_inner_width=100)

    self.assertEqual(config.chrome_window.origin.x, 0)
    self.assertEqual(config.chrome_window.width, 100)
    self.assertEqual(config.chrome_window.origin.y, 10)
    self.assertEqual(config.chrome_window.height, 90)

  def test_calculate_coordinates_chrome_higher_pixel_ratio_calculated_correctly(
      self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        window_inner_height=400,
        window_inner_width=400,
        element_rect=DisplayRectangle(Point(196, 196), 8, 8))

    element_center = config.element_center()
    assert element_center
    self.assertEqual(element_center.x, 50)
    self.assertEqual(element_center.y, 50)

    self.assertEqual(config.css_to_native_distance(60), 15)

  def test_calculate_coordinates_chrome_lower_pixel_ratio_calculated_correctly(
      self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(0, 0), 600, 600),
        window_inner_height=200,
        window_inner_width=200,
        element_rect=DisplayRectangle(Point(99, 99), 2, 2))

    element_center = config.element_center()
    assert element_center
    self.assertEqual(element_center.x, 300)
    self.assertEqual(element_center.y, 300)

    self.assertEqual(config.css_to_native_distance(60), 180)

  def test_calculate_coordinates_chrome_window_offset_accounted_for(self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(100, 200), 100, 100),
        window_inner_height=100,
        window_inner_width=100,
        element_rect=DisplayRectangle(Point(49, 49), 2, 2))

    element_center = config.element_center()
    assert element_center
    self.assertEqual(element_center.x, 150)
    self.assertEqual(element_center.y, 250)

  def test_calculate_coordinates_element_center_calculated_correctly(self):
    config: ViewportInfo = ViewportInfo(
        raw_chrome_window_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        window_inner_height=100,
        window_inner_width=100,
        element_rect=DisplayRectangle(Point(10, 20), 80, 70))

    element_center = config.element_center()
    assert element_center
    self.assertEqual(element_center.x, 50)
    self.assertEqual(element_center.y, 55)


class AndroidInputActionRunnerTestCase(ActionRunnerTestCase):
  __test__ = True

  @override
  def setUp(self) -> None:
    super().setUp()
    self.host_platform = LinuxMockPlatform()
    self.fs.create_file("/usr/bin/adb", contents="adb")
    self.host_platform.expect_sh(
        "/usr/bin/adb",
        "devices",
        "-l",
        result=("List of attached devices\n"
                "1.1.1.1 device product:mock model:mock"))
    self.platform = AndroidAdbMockPlatform(
        self.host_platform, adb=MockAdb(self.host_platform))
    self.browser = MockChromeAndroidStable(
        "mock browser", settings=Settings(platform=self.platform))
    self.runner = MockRunner()
    self.root_dir = pathlib.Path()
    self.session = BrowserSessionRunGroup(self.runner.env,
                                          self.runner.probes, self.browser,
                                          Flags(), 1, self.root_dir, True, True)
    self.mock_run = MockRun(self.runner, self.session, "run 1")
    self.action_runner = AndroidInputActionRunner(self.mock_run)
    self.mock_run.action_runner = self.action_runner

  def run_action(self, action: Action) -> None:
    action.run_with(self.action_runner)

  def expect_action_setup(
      self,
      found_element: bool = True,
      js_args: tuple[str, bool] | None = None,
      app_bounds: DisplayRectangle = DisplayRectangle(Point(0, 0), 10, 10),
      window_inner_height: int | None = None,
      window_inner_width: int | None = None,
      element_bounds: DisplayRectangle = DisplayRectangle(Point(0, 0), 0, 0)):
    self.platform.expect_sh(
        "dumpsys",
        "window",
        "windows",
        result=(f"chrome.Main\n"
                f"mAppBounds=Rect({app_bounds.left}, "
                f"{app_bounds.top} - {app_bounds.right}, {app_bounds.bottom})"))

    if not window_inner_height:
      window_inner_height = app_bounds.height

    if not window_inner_width:
      window_inner_width = app_bounds.width

    # element bounding rect
    self.browser.expect_js(
        JsInvocation(
            result=[
                # Found element
                found_element,
                # window.innerHeight
                window_inner_height,
                # window.innerWidth
                window_inner_width,
                # rect.left
                element_bounds.left,
                # rect.top
                element_bounds.top,
                # rect.width
                element_bounds.width,
                # rect.height
                element_bounds.height,
            ],
            arguments=js_args))

  def test_swipe(self):
    self.platform.expect_sh("input", "swipe", "0", "1", "2", "3", "3000")
    swipe_action = SwipeAction.create(
        0, 1, 2, 3, duration=dt.timedelta(milliseconds=3000))
    self.run_action(swipe_action)

  def test_text_input_zero_duration(self):
    self.platform.expect_sh("input", "keyboard", "text", "Some%ssample%stext")
    text_input_action = TextInputAction.create(
        InputSource.KEYBOARD, text="Some sample text", duration=dt.timedelta())
    self.assertFalse(self.runner.mock_waits)
    self.run_action(text_input_action)
    self.assertFalse(self.runner.mock_waits)

  def test_text_input_non_zero_duration(self):
    text_input_action = TextInputAction.create(
        InputSource.KEYBOARD, text="aaa", duration=dt.timedelta(seconds=1))
    for _ in range(3):
      self.platform.expect_sh("input", "keyboard", "text", "a")
    self.assertFalse(self.runner.mock_waits)
    self.run_action(text_input_action)
    self.assertTrue(self.runner.mock_waits)

  def test_click_touch_coordinates(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_coordinates(x=100, y=200))

    self.platform.expect_sh("input", "tap", "100", "200")

    self.run_action(click_action)

  def test_click_mouse_coordinates(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        position=PositionConfig.from_coordinates(x=100, y=200))

    self.platform.expect_sh("input", "mouse", "tap", "100", "200")

    self.run_action(click_action)

  def test_click_mouse_non_zero_duration_fails(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        duration=dt.timedelta(seconds=1),
        position=PositionConfig.from_coordinates(x=0, y=0))

    with self.assertRaises(InputSourceNotImplementedError) as cm:
      self.run_action(click_action)
    self.assertIn("Non-zero", str(cm.exception))

  def test_click_touch_non_zero_duration_fails(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        duration=dt.timedelta(seconds=1),
        position=PositionConfig.from_coordinates(x=0, y=0))

    with self.assertRaises(InputSourceNotImplementedError) as cm:
      self.run_action(click_action)
    self.assertIn("Non-zero", str(cm.exception))

  def test_click_selector_passes_selector_string(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_selector(selector="div[]", required=False))

    self.expect_action_setup(found_element=False, js_args=["div[]", False])

    self.run_action(click_action)

  def test_click_selector_scroll_into_view_passes_scroll_true(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_selector(
            selector="div[]", required=False, scroll_into_view=True))

    self.expect_action_setup(found_element=False, js_args=["div[]", True])

    self.run_action(click_action)

  def test_click_selector_non_existant_element_raises(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_selector(selector="div[]", required=True))

    self.expect_action_setup(found_element=False)

    with self.assertRaises(ElementNotFoundError) as cm:
      self.run_action(click_action)
    self.assertIn("matching DOM", str(cm.exception))

  def test_click_touch_selector_non_required_element_success(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_selector(selector="div[]", required=False))

    self.expect_action_setup(found_element=False)

    self.run_action(click_action)

  def test_click_mouse_selector_non_required_element_success(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        position=PositionConfig.from_selector(selector="div[]", required=False))

    self.expect_action_setup(found_element=False)

    self.run_action(click_action)

  def test_click_touch_selector_success(self):
    click_action = ClickAction.create(
        InputSource.TOUCH,
        position=PositionConfig.from_selector(selector="div[]", required=True))

    self.expect_action_setup(
        found_element=True,
        app_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        element_bounds=DisplayRectangle(Point(20, 40), 10, 10))

    self.platform.expect_sh("input", "tap", "25", "45")

    self.run_action(click_action)

  def test_click_mouse_selector_success(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        position=PositionConfig.from_selector(selector="div[]", required=True))

    self.expect_action_setup(
        found_element=True,
        app_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        element_bounds=DisplayRectangle(Point(20, 40), 10, 10))

    self.platform.expect_sh("input", "mouse", "tap", "25", "45")

    self.run_action(click_action)

  def test_click_wait_timeout_required(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        position=PositionConfig.from_selector(
            selector="#selector", required=True, wait=True),
        # Set timeout to 0.1 to timeout after 1 call to wait_for_element_impl.
        timeout=dt.timedelta(seconds=0.1))
    self.browser.expect_js(JsInvocation(arguments=("#selector",), result=False))

    with self.assertRaises(TimeoutError):
      self.run_action(click_action)

  def test_click_wait_timeout_unrequired(self):
    click_action = ClickAction.create(
        InputSource.MOUSE,
        position=PositionConfig.from_selector(
            selector="#selector", required=False, wait=True),
        # Set timeout to 0.1 to timeout after 1 call to wait_for_element_impl.
        timeout=dt.timedelta(seconds=0.1))
    self.browser.expect_js(JsInvocation(arguments=("#selector",), result=False))

    # We continue to execute the click even after the wait fails.
    self.expect_action_setup(found_element=False)

    self.run_action(click_action)

  def test_scroll_selector_non_required_element_success(self):
    scroll_action = ScrollAction.create(
        InputSource.TOUCH, distance=100, selector="div[]", required=False)

    self.expect_action_setup(found_element=False)

    self.run_action(scroll_action)

  def test_scroll_touch_selector_non_existant_element_raises(self):
    scroll_action = ScrollAction.create(
        InputSource.TOUCH, distance=100, selector="div[]", required=True)

    self.expect_action_setup(found_element=False)

    with self.assertRaises(ElementNotFoundError) as cm:
      self.run_action(scroll_action)
    self.assertIn("matching DOM", str(cm.exception))

  def test_scroll_distance_converted_to_css_pixels(self):
    scroll_action = ScrollAction.create(InputSource.TOUCH, distance=100)

    self.expect_action_setup(
        found_element=False,
        app_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        window_inner_height=200,
        window_inner_width=200)

    self.platform.expect_sh("input", "swipe", "50", "90", "50", "40", "1000")

    self.run_action(scroll_action)

  def test_scroll_positive_direction(self):
    scroll_action = ScrollAction.create(InputSource.TOUCH, distance=1)

    self.expect_action_setup(
        found_element=False, app_bounds=DisplayRectangle(Point(0, 0), 10, 10))

    self.platform.expect_sh("input", "swipe", "5", "9", "5", "8", "1000")

    self.run_action(scroll_action)

  def test_scroll_negative_direction(self):
    scroll_action = ScrollAction.create(InputSource.TOUCH, distance=-1)

    self.expect_action_setup(
        found_element=False, app_bounds=DisplayRectangle(Point(0, 0), 10, 10))

    self.platform.expect_sh("input", "swipe", "5", "1", "5", "2", "1000")

    self.run_action(scroll_action)

  def test_scroll_window_scrolls_window_bounds(self):
    scroll_action = ScrollAction.create(InputSource.TOUCH, distance=80)

    self.expect_action_setup(
        found_element=False, app_bounds=DisplayRectangle(Point(0, 0), 100, 100))

    self.platform.expect_sh("input", "swipe", "50", "90", "50", "10", "1000")

    self.run_action(scroll_action)

  def test_scroll_element_scrolls_element_bounds(self):
    scroll_action = ScrollAction.create(
        InputSource.TOUCH, distance=10, selector="div[]", required=True)

    self.expect_action_setup(
        found_element=True,
        app_bounds=DisplayRectangle(Point(0, 0), 100, 100),
        element_bounds=DisplayRectangle(Point(10, 10), 80, 80))

    self.platform.expect_sh("input", "swipe", "50", "82", "50", "72", "1000")

    self.run_action(scroll_action)

  def test_scroll_touch_duration_single_scroll(self):
    scroll_action = ScrollAction.create(
        InputSource.TOUCH,
        distance=80,
        duration=dt.timedelta(milliseconds=3000))

    self.expect_action_setup(
        found_element=False, app_bounds=DisplayRectangle(Point(0, 0), 100, 100))

    self.platform.expect_sh("input", "swipe", "50", "90", "50", "10", "3000")

    self.run_action(scroll_action)

  def test_scroll_is_chunked(self):
    scroll_action = ScrollAction.create(InputSource.TOUCH, distance=999)

    self.expect_action_setup(
        found_element=False, app_bounds=DisplayRectangle(Point(0, 0), 100, 100))

    for _ in range(12):
      self.platform.expect_sh("input", "swipe", "50", "90", "50", "10", "80")

    self.platform.expect_sh("input", "swipe", "50", "90", "50", "51", "39")

    self.run_action(scroll_action)

  def test_click_ui_selector_required_element_not_found_raises(self):
    click_action = ClickAction(
        InputSource.TOUCH,
        position=PositionConfig.from_ui_selector(
            text="mock_target", required=True))

    mock_ad = mock.MagicMock()
    mock_ui_object = mock.MagicMock()
    mock_ui_object.wait.exists.return_value = False
    mock_ad.ui.return_value = mock_ui_object

    with mock.patch.object(
        self.platform,
        "uiautomator_device",
        return_value=contextlib.nullcontext(mock_ad)):
      with self.assertRaises(AssertionError) as cm:
        self.run_action(click_action)
      self.assertIn("Element with selector", str(cm.exception))
      self.assertIn("mock_target", str(cm.exception))
      mock_ad.ui.assert_called_once_with(text="mock_target")
      mock_ui_object.click.assert_not_called()

  def test_click_ui_selector_non_required_element_not_found_success(self):
    click_action = ClickAction(
        InputSource.TOUCH,
        position=PositionConfig.from_ui_selector(
            text="mock_target", required=False))

    mock_ad = mock.MagicMock()
    mock_ui_object = mock.MagicMock()
    mock_ui_object.wait.exists.return_value = False
    mock_ad.ui.return_value = mock_ui_object

    with mock.patch.object(
        self.platform,
        "uiautomator_device",
        return_value=contextlib.nullcontext(mock_ad)):
      self.run_action(click_action)
      mock_ad.ui.assert_called_once_with(text="mock_target")
      mock_ui_object.click.assert_not_called()

  def test_click_ui_selector_required_element_found_clicks(self):
    click_action = ClickAction(
        InputSource.TOUCH,
        position=PositionConfig.from_ui_selector(res="mock_res", required=True))

    mock_ad = mock.MagicMock()
    mock_ui_object = mock.MagicMock()
    mock_ui_object.wait.exists.return_value = True
    mock_ad.ui.return_value = mock_ui_object

    with mock.patch.object(
        self.platform,
        "uiautomator_device",
        return_value=contextlib.nullcontext(mock_ad)):
      self.run_action(click_action)
      mock_ad.ui.assert_called_once_with(res="mock_res")
      mock_ui_object.click.assert_called_once()

  def test_click_ui_selector_non_required_element_found_clicks(self):
    click_action = ClickAction(
        InputSource.TOUCH,
        position=PositionConfig.from_ui_selector(
            clazz="mock_clazz", required=False))

    mock_ad = mock.MagicMock()
    mock_ui_object = mock.MagicMock()
    mock_ui_object.wait.exists.return_value = True
    mock_ad.ui.return_value = mock_ui_object

    with mock.patch.object(
        self.platform,
        "uiautomator_device",
        return_value=contextlib.nullcontext(mock_ad)):
      self.run_action(click_action)
      mock_ad.ui.assert_called_once_with(clazz="mock_clazz")
      mock_ui_object.click.assert_called_once()

  def test_click_ui_selector_pkg_and_text(self):
    click_action = ClickAction(
        InputSource.TOUCH,
        position=PositionConfig.from_ui_selector(
            pkg="com.android.systemui", text="Chrome", required=True))

    mock_ad = mock.MagicMock()
    mock_ui_object = mock.MagicMock()
    mock_ui_object.wait.exists.return_value = True
    mock_ad.ui.return_value = mock_ui_object

    with mock.patch.object(
        self.platform,
        "uiautomator_device",
        return_value=contextlib.nullcontext(mock_ad)):
      self.run_action(click_action)
      mock_ad.ui.assert_called_once_with(
          pkg="com.android.systemui", text="Chrome")
      mock_ui_object.click.assert_called_once()


if __name__ == "__main__":
  test_helper.run_pytest(__file__)
