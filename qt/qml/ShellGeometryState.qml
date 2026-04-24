import QtQuick 6.5

QtObject {
    id: root

    required property var frame
    required property bool leftRailCollapsed
    required property bool rightInspectorCollapsed
    required property bool bottomStripCollapsed
    required property real availableContentWidth
    required property real availableContentHeight

    property int appMinWidth: Number(frame?.app_min_width_px ?? 1440)
    property int appMinHeight: Number(frame?.app_min_height_px ?? 900)
    property int statusStripHeight: Number(frame?.status_strip_height_px ?? 40)
    property int tabRailHeight: Number(frame?.tab_rail_height_px ?? 32)
    property int splitterThickness: 12
    property int leftRailDefaultWidth: Number(frame?.left_rail_default_width_px ?? 272)
    property int leftRailCollapsedWidth: Number(frame?.left_rail_collapsed_width_px ?? 40)
    property int leftRailMinWidth: Number(frame?.left_rail_min_width_px ?? 232)
    property int rightInspectorDefaultWidth: Number(frame?.right_inspector_default_width_px ?? 360)
    property int rightInspectorCollapsedWidth: Number(frame?.right_inspector_collapsed_width_px ?? 40)
    property int rightInspectorMinWidth: Number(frame?.right_inspector_min_width_px ?? 320)
    property int bottomStripDefaultHeight: Number(frame?.bottom_strip_default_height_px ?? 192)
    property int bottomStripCollapsedHeight: Number(frame?.bottom_strip_collapsed_height_px ?? 28)
    property int bottomStripMinHeight: Number(frame?.bottom_strip_min_height_px ?? 144)
    property int centerMinWidth: Number(frame?.center_min_width_px ?? 720)
    property int centerMinHeight: Number(frame?.center_min_height_px ?? 420)

    property int leftRailWidthOverride: -1
    property int rightInspectorWidthOverride: -1
    property int bottomStripHeightOverride: -1

    property int leftRailWidth: leftRailCollapsed
        ? leftRailCollapsedWidth
        : Math.round(leftRailWidthOverride > 0 ? leftRailWidthOverride : leftRailDefaultWidth)
    property int rightInspectorWidth: rightInspectorCollapsed
        ? rightInspectorCollapsedWidth
        : Math.round(rightInspectorWidthOverride > 0 ? rightInspectorWidthOverride : rightInspectorDefaultWidth)
    property int bottomStripHeight: bottomStripCollapsed
        ? bottomStripCollapsedHeight
        : Math.round(bottomStripHeightOverride > 0 ? bottomStripHeightOverride : bottomStripDefaultHeight)

    function clamp(value, minValue, maxValue) {
        const boundedMax = Math.max(minValue, maxValue)
        return Math.round(Math.max(minValue, Math.min(value, boundedMax)))
    }

    function horizontalReservedWidth() {
        return (!leftRailCollapsed ? splitterThickness : 0)
            + (!rightInspectorCollapsed ? splitterThickness : 0)
    }

    function verticalReservedHeight() {
        return !bottomStripCollapsed ? splitterThickness : 0
    }

    function clampLeftRailWidth(value, currentRightInspectorWidth) {
        return clamp(
            value,
            leftRailMinWidth,
            availableContentWidth - currentRightInspectorWidth - centerMinWidth - horizontalReservedWidth()
        )
    }

    function clampRightInspectorWidth(value, currentLeftRailWidth) {
        return clamp(
            value,
            rightInspectorMinWidth,
            availableContentWidth - currentLeftRailWidth - centerMinWidth - horizontalReservedWidth()
        )
    }

    function clampBottomStripHeight(value) {
        return clamp(
            value,
            bottomStripMinHeight,
            availableContentHeight - centerMinHeight - verticalReservedHeight()
        )
    }

    function setLeftRailWidth(value) {
        leftRailWidthOverride = clampLeftRailWidth(value, rightInspectorWidth)
    }

    function setRightInspectorWidth(value) {
        rightInspectorWidthOverride = clampRightInspectorWidth(value, leftRailWidth)
    }

    function setBottomStripHeight(value) {
        bottomStripHeightOverride = clampBottomStripHeight(value)
    }

    function resetLeftRailWidth() {
        leftRailWidthOverride = -1
    }

    function resetRightInspectorWidth() {
        rightInspectorWidthOverride = -1
    }

    function resetBottomStripHeight() {
        bottomStripHeightOverride = -1
    }
}
