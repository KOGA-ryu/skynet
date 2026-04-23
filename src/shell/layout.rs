use crate::shell::{PaneGeometry, ShellLayout};

pub fn first_vertical_slice_layout() -> ShellLayout {
    ShellLayout {
        app_min_width_px: 1440,
        app_min_height_px: 900,
        status_strip_height_px: 40,
        tab_rail_height_px: 32,
        left_rail: PaneGeometry {
            default_px: 272,
            min_px: 232,
            max_px: Some(360),
            collapsed_px: 40,
            collapsible: true,
        },
        right_inspector: PaneGeometry {
            default_px: 360,
            min_px: 320,
            max_px: Some(480),
            collapsed_px: 40,
            collapsible: true,
        },
        bottom_strip: PaneGeometry {
            default_px: 192,
            min_px: 144,
            max_px: Some(320),
            collapsed_px: 28,
            collapsible: true,
        },
        center_min_width_px: 720,
        center_min_height_px: 420,
    }
}
