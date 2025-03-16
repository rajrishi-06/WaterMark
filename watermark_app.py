import os
import sys

# 1) Define resource_path to locate files when using PyInstaller or running normally.
def resource_path(relative_path):
    """Get absolute path to resource, works for dev and for PyInstaller."""
    if hasattr(sys, '_MEIPASS'):
        return os.path.join(sys._MEIPASS, relative_path)
    return os.path.join(os.path.abspath("."), relative_path)

# For macOS, if the tkdnd2.8 folder is already in /Library, use that.
if sys.platform == "darwin" and os.path.exists("/Library/tkdnd2.8"):
    tkdnd_folder = "/Library/tkdnd2.8"
else:
    tkdnd_folder = resource_path("tkdnd2.8")
os.environ["TCLLIBPATH"] = tkdnd_folder

# Import tkinterdnd2 after TCLLIBPATH is set.
from tkinterdnd2 import TkinterDnD, DND_FILES
import tkinter as tk
from tkinter import filedialog, colorchooser, messagebox
from PIL import Image, ImageDraw, ImageTk, ImageFont, ImageColor

# Global variables for the main image and processed image.
original_image = None
processed_image = None

# ---------------- Main Image Functions ----------------

def load_image(file_path):
    """Load the main image and show watermark options."""
    global original_image, processed_image
    try:
        original_image = Image.open(file_path)
        processed_image = original_image.copy()
        show_options_frame()
        # Change the drag-and-drop label text to indicate successful upload
        dragAndDrop.config(text="Image Uploaded")
    except Exception as e:
        print(f"Error loading image: {e}")

def on_main_image_drop(event):
    """Drag-and-drop handler for the main image."""
    file_path = event.data.strip()
    if file_path.startswith("{") and file_path.endswith("}"):
        file_path = file_path[1:-1]
    load_image(file_path)

def upload_main_image():
    file_path = filedialog.askopenfilename(
        filetypes=(("Image Files", "*.png *.jpg *.jpeg *.gif *.bmp"), ("All Files", "*.*"))
    )
    if file_path:
        load_image(file_path)

def show_options_frame():
    """Show the radio buttons and Apply button after the main image is loaded."""
    options_frame.pack(side="top", fill="x", padx=20, pady=10)

# ---------------- Apply Operations ----------------

def apply_changes():
    """Apply the selected operation."""
    global processed_image
    if original_image is None:
        messagebox.showerror("Error", "No image selected. Please upload an image first.")
        return
    choice = var_option.get()
    if choice == "tilt":
        try:
            angle = float(tilt_entry.get())
        except ValueError:
            angle = 90  # Fallback to default if conversion fails
        processed_image = original_image.copy().rotate(angle, expand=True)
        open_preview_window(processed_image)
    elif choice == "img_watermark":
        open_logo_window()  # Prompt for logo (drag/drop or browse)
    elif choice == "text_watermark":
        open_text_input_window()  # Prompt for watermark text & color
    elif choice == "water_over":
        open_water_over_window()  # Prompt for angle for tiled watermark

# ---------------- Image Watermark Functions ----------------

def open_logo_window():
    """Open a window to let the user supply a logo image (drag-and-drop or browse)."""
    logo_win = tk.Toplevel(window)
    logo_win.title("Choose Logo for Watermark")
    instruction = tk.Label(logo_win, text="Drag and drop your logo here or click Browse:")
    instruction.pack(padx=10, pady=10)
    logo_drop_label = tk.Label(logo_win, text="Drop Logo Here", bg="lightgray", width=40, height=5)
    logo_drop_label.pack(padx=10, pady=10)
    logo_drop_label.drop_target_register(DND_FILES)
    logo_drop_label.dnd_bind("<<Drop>>", lambda e: logo_drop_handler(e, logo_win))
    browse_btn = tk.Button(logo_win, text="Browse Logo", command=lambda: browse_logo(logo_win))
    browse_btn.pack(pady=10)

def logo_drop_handler(event, logo_win):
    """Handle drop event for logo image."""
    file_path = event.data.strip()
    if file_path.startswith("{") and file_path.endswith("}"):
        file_path = file_path[1:-1]
    apply_logo_watermark(file_path)
    open_preview_window(processed_image)
    logo_win.destroy()

def browse_logo(logo_win):
    """Browse for a logo image."""
    file_path = filedialog.askopenfilename(
        filetypes=(("Image Files", "*.png *.jpg *.jpeg *.gif *.bmp"), ("All Files", "*.*"))
    )
    if file_path:
        apply_logo_watermark(file_path)
        open_preview_window(processed_image)
        logo_win.destroy()

def apply_logo_watermark(logo_path):
    """Apply an image watermark using the provided logo image.
       The logo is scaled to ~20% of the main image's width."""
    global processed_image
    processed_image = original_image.copy()
    try:
        logo_img = Image.open(logo_path).convert("RGBA")
        main_w, main_h = processed_image.size
        scaling_factor = 0.2
        new_logo_width = int(main_w * scaling_factor)
        ratio = new_logo_width / logo_img.width
        new_logo_height = int(logo_img.height * ratio)
        logo_img = logo_img.resize((new_logo_width, new_logo_height), Image.LANCZOS)
        x = main_w - new_logo_width - 10
        y = main_h - new_logo_height - 10
        processed_image.paste(logo_img, (x, y), logo_img)
    except Exception as e:
        print(f"Error applying logo watermark: {e}")

# ---------------- Text Watermark Functions ----------------

def open_text_input_window():
    """Open a window prompting the user for watermark text."""
    text_win = tk.Toplevel(window)
    text_win.title("Enter Watermark Text")
    label = tk.Label(text_win, text="Enter watermark text:")
    label.pack(padx=10, pady=10)
    entry = tk.Entry(text_win, width=30)
    entry.pack(padx=10, pady=10)
    entry.focus_set()
    apply_btn = tk.Button(text_win, text="Apply", command=lambda: text_watermark_handler(entry.get(), text_win))
    apply_btn.pack(pady=10)

def text_watermark_handler(watermark_text, text_win):
    """Apply text watermark using the provided text."""
    if watermark_text.strip() == "":
        watermark_text = "Watermark"
    apply_text_watermark(watermark_text)
    open_preview_window(processed_image)
    text_win.destroy()

def apply_text_watermark(watermark_text):
    """Apply a text watermark responsively based on main image size.
       The font size is set to ~15% of main image width.
       The user is prompted to choose a watermark color."""
    global processed_image
    processed_image = original_image.copy()
    draw = ImageDraw.Draw(processed_image)
    main_w, main_h = processed_image.size

    # Compute a responsive font size (~4% of main dimension).
    font_size = max(int(main_h * 0.04), int(main_w * 0.04))

    # Use resource_path to load the font from 'fonts' folder.
    font_path = resource_path("fonts/boom.ttf")
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        print("Using default font; true type font not found.", e)
        font = ImageFont.load_default()

    # Prompt the user to pick a color for the watermark text.
    color_result = colorchooser.askcolor(title="Choose Watermark Color")
    watermark_color = color_result[1] if color_result[1] is not None else "white"

    # Calculate text size using textbbox (returns left, top, right, bottom)
    left, top, right, bottom = draw.textbbox((0, 0), watermark_text, font=font)
    text_width = right - left
    text_height = bottom - top

    # Position text at bottom-right with a 25-pixel margin
    x = main_w - text_width - 25
    y = main_h - text_height - 25

    # Draw an outline for readability
    outline_range = 2
    for dx in range(-outline_range, outline_range + 1):
        for dy in range(-outline_range, outline_range + 1):
            draw.text((x + dx, y + dy), watermark_text, font=font, fill="black")

    # Draw the main text in the chosen color
    draw.text((x, y), watermark_text, font=font, fill=watermark_color)

# ---------------- Water All Over Functions ----------------

def open_water_over_window():
    """Open a window prompting the user to enter watermark text, choose a color, and enter a tilt angle."""
    water_over_win = tk.Toplevel(window)
    water_over_win.title("Water Over Image Options")

    # Watermark Text
    text_label = tk.Label(water_over_win, text="Enter watermark text:")
    text_label.pack(padx=10, pady=5)
    text_entry = tk.Entry(water_over_win, width=30)
    text_entry.insert(0, "SAMPLE WATERMARK")
    text_entry.pack(padx=10, pady=5)

    # Watermark Color
    color_label = tk.Label(water_over_win, text="Choose watermark color:")
    color_label.pack(padx=10, pady=5)
    # Use a list to hold the mutable selected color.
    selected_color = ["#FFFFFF"]  # default white

    def choose_color():
        color_result = colorchooser.askcolor(title="Choose Watermark Color")
        if color_result[1]:
            selected_color[0] = color_result[1]
            color_btn.config(bg=selected_color[0])

    color_btn = tk.Button(water_over_win, text="Choose Color", command=choose_color, bg=selected_color[0])
    color_btn.pack(padx=10, pady=5)

    # Tilt Angle
    angle_label = tk.Label(water_over_win, text="Enter tilt angle:")
    angle_label.pack(padx=10, pady=5)
    angle_entry = tk.Entry(water_over_win, width=10)
    angle_entry.insert(0, "0")
    angle_entry.pack(padx=10, pady=5)

    # Apply Button
    apply_btn = tk.Button(
        water_over_win, text="Apply",
        command=lambda: water_over_handler(text_entry.get(), angle_entry.get(), selected_color[0], water_over_win)
    )
    apply_btn.pack(pady=10)

def water_over_handler(text_str, angle_str, chosen_color, water_over_win):
    """Handle the water-over prompt inputs and apply the tiled watermark."""
    try:
        angle = float(angle_str)
    except ValueError:
        angle = 0
    apply_water_over(text_str, chosen_color, angle)
    open_preview_window(processed_image)
    water_over_win.destroy()

def apply_water_over(text, color, angle):
    """Apply a tiled text watermark over the entire image with low opacity."""
    global processed_image
    base = original_image.convert("RGBA")
    overlay = Image.new("RGBA", base.size, (0, 0, 0, 0))
    main_w, main_h = base.size

    # Calculate responsive font size
    font_size = max(int(main_h * 0.025), int(main_w * 0.025))

    # Use resource_path to load the font from 'fonts' folder.
    font_path = resource_path("fonts/medium.ttf")
    try:
        font = ImageFont.truetype(font_path, font_size)
    except Exception as e:
        print("Using default font for water-over; true type font not found.", e)
        font = ImageFont.load_default()

    # Create a text image of the watermark text.
    dummy_draw = ImageDraw.Draw(overlay)
    left, top, right, bottom = dummy_draw.textbbox((0, 0), text, font=font)
    text_width = int(right - left)
    text_height = int(bottom - top + 10)

    text_img = Image.new("RGBA", (text_width, text_height), (0, 0, 0, 0))
    text_draw = ImageDraw.Draw(text_img)

    # Convert the chosen color (hex) to RGBA with some transparency (alpha=170)
    rgb_color = ImageColor.getrgb(color)
    text_draw.text((0, 0), text, font=font, fill=rgb_color + (170,))

    # Rotate the text image by the given angle.
    rotated_text_img = text_img.rotate(angle, expand=True)

    # Increase gap between watermarks (sparser tiling)
    gap_x = 150
    gap_y = 150
    step_x = rotated_text_img.width + gap_x
    step_y = rotated_text_img.height + gap_y

    # Tile the rotated text image over the overlay.
    for x in range(0, main_w, step_x):
        for y in range(0, main_h, step_y):
            overlay.paste(rotated_text_img, (x, y), rotated_text_img)

    # Composite overlay onto base
    processed_image = Image.alpha_composite(base, overlay)

# ---------------- Preview and Save Functions ----------------

def open_preview_window(full_res_image):
    """Open a preview window showing a scaled-down version of the processed image."""
    display_image = full_res_image.copy()
    display_image.thumbnail((600, 600))
    preview_win = tk.Toplevel(window)
    preview_win.title("Preview")
    tk_img = ImageTk.PhotoImage(display_image)
    label = tk.Label(preview_win, image=tk_img)
    label.pack(padx=10, pady=10)
    label.image = tk_img
    save_btn = tk.Button(preview_win, text="Save Image", command=lambda: save_image(full_res_image))
    save_btn.pack(pady=5)

def save_image(pil_image):
    """Prompt the user to save the full-resolution processed image."""
    file_path = filedialog.asksaveasfilename(
        defaultextension=".png",
        filetypes=[("PNG Files", "*.png"), ("JPEG Files", "*.jpg;*.jpeg"), ("All Files", "*.*")]
    )
    if file_path:
        try:
            pil_image.save(file_path)
            print(f"Image saved to {file_path}")
        except Exception as e:
            print(f"Error saving image: {e}")

# ---------------- Main GUI Setup ----------------

window = TkinterDnD.Tk()
window.title("Drag & Drop")
window.minsize(600, 400)

# Top frame with rounded-corner area for main image drag-and-drop.
top_frame = tk.Frame(window, bg="white")
top_frame.pack(side="top", fill="both", expand=True, padx=20, pady=20)
width, height = 500, 250
corner_radius = 40
bg_color = (240, 128, 128)
bg_img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
draw_bg = ImageDraw.Draw(bg_img)
draw_bg.rounded_rectangle((0, 0, width, height), radius=corner_radius, fill=bg_color)
rounded_bg = ImageTk.PhotoImage(bg_img)
dragAndDrop = tk.Label(
    top_frame,
    image=rounded_bg,
    text="Drag & Drop",
    compound="center",
    fg="black",
    font=("Times New Roman", 48, "bold")
)
dragAndDrop.pack(expand=True, fill="both")
dragAndDrop.bg_image = rounded_bg
dragAndDrop.drop_target_register(DND_FILES)
dragAndDrop.dnd_bind("<<Drop>>", on_main_image_drop)

# ---------------- Bottom Frame with Options ----------------

bottom_frame = tk.Frame(window)
bottom_frame.pack(side="bottom", fill="x", padx=20, pady=10)
upload_btn = tk.Button(bottom_frame, text="Upload Main Image", command=upload_main_image)
upload_btn.pack(side="top", pady=20)

options_frame = tk.Frame(bottom_frame)
var_option = tk.StringVar(value="tilt")

# Tilt Option with Angle Entry
radio_tilt = tk.Radiobutton(options_frame, text="Tilt Image ↺", variable=var_option, value="tilt")
radio_tilt.pack(side="left", padx=5)
tilt_label = tk.Label(options_frame, text="Angle:")
tilt_label.pack(side="left", padx=(5, 0))
tilt_entry = tk.Entry(options_frame, width=5)
tilt_entry.insert(0, "90")  # Default angle
tilt_entry.pack(side="left")

# Other Options
radio_img_wm = tk.Radiobutton(options_frame, text="Add Image Watermark", variable=var_option, value="img_watermark")
radio_img_wm.pack(side="left", padx=5)
radio_text_wm = tk.Radiobutton(options_frame, text="Add Text Watermark", variable=var_option, value="text_watermark")
radio_text_wm.pack(side="left", padx=5)
radio_water_over = tk.Radiobutton(options_frame, text="WaterMark all over", variable=var_option, value="water_over")
radio_water_over.pack(side="left", padx=5)

# Apply Button
apply_btn = tk.Button(options_frame, text="Apply", command=apply_changes)
apply_btn.pack(side="left", padx=10)

options_frame.pack()  # Make sure the options frame is visible

window.mainloop()
