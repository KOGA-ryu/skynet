import QtQuick 6.5

QtObject {
    id: root

    required property var frame
    required property bool leftRailCollapsed
    required property bool rightInspectorCollapsed
    required property bool bottomStripCollapsed

    property int appMinWidth: Number(frame?.app_min_width_px ?? 1440)
    property int appMinHeight: Number(frame?.app_min_height_px ?? 900)
    property int statusStripHeight: Number(frame?.status_strip_height_px ?? 40)
    property int tabRailHeight: Number(frame?.tab_rail_height_px ?? 32)
    property int leftRailWidth: leftRailCollapsed
        ? Number(frame?.left_rail_collapsed_width_px ?? 40)
        : Number(frame?.left_rail_default_width_px ?? 272)
    property int rightInspectorWidth: rightInspectorCollapsed
        ? Number(frame?.right_inspector_collapsed_width_px ?? 40)
        : Number(frame?.right_inspector_default_width_px ?? 360)
    property int bottomStripHeight: bottomStripCollapsed
        ? Number(frame?.bottom_strip_collapsed_height_px ?? 28)
        : Number(frame?.bottom_strip_default_height_px ?? 192)
}
