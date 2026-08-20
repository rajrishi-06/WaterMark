# Screens

Every screen, captured from the running application. Each was driven
programmatically and asserted on — see `tests/test_ui.py` for the checks that
keep them honest.

## Text tab — sections expanded
Font, size, colour, opacity and rotation, with Outline, Drop shadow and
Backdrop plate open. Tokens in the text box are expanded live in the preview.

![Text tab](screens/05_text_sections_expanded.png)

## Logo tab
Scale, opacity, rotation and greyscale for an image watermark, with the
nine-point position pad.

![Logo tab](screens/01_logo_tab.png)

## Pattern tab
The repeating wash, built from either the text or the logo, with angle,
spacing, opacity and row stagger.

![Pattern tab](screens/02_pattern_tab.png)

## Adjust tab
Rotation and flips are applied *before* the watermark, so the watermark always
stays the right way up — visible here on a 90°-rotated, flipped frame.

![Adjust tab](screens/03_adjust_tab.png)

## Export tab
Format, quality, resize and the "fit under a file size" budget. Controls that
do not apply to the current format or resize mode are greyed out.

![Export tab](screens/04_export_tab.png)

## Presets
Nine built-ins plus anything you save.

![Presets menu](screens/07_presets_menu.png)

## Saving a preset
Built-in names are refused inline rather than after the fact.

![Save preset dialog](screens/06_save_preset_dialog.png)

## Batch
Files or folders in, an output folder and a name pattern out, with live
progress and a summary of what it saved.

![Batch dialog](screens/12_batch_complete.png)

## Preferences
Theme, preview quality, worker count — and a shortcut to wherever your
settings, presets and logs actually live.

![Preferences](screens/08_preferences_dialog.png)

## Light theme
The whole window rebuilds; your image, settings and history survive.

![Light theme](screens/10_light_theme.png)

## Help

![Keyboard shortcuts](screens/09_shortcuts.png)

![About](screens/11_about.png)
