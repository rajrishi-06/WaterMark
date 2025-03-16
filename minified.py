_S='Drag & Drop'
_R='Choose Watermark Color'
_Q='Enter watermark text:'
_P='<<Drop>>'
_O='water_over'
_N='text_watermark'
_M='img_watermark'
_L='*.png *.jpg *.jpeg *.gif *.bmp'
_K='Image Files'
_J='/Library/tkdnd2.8'
_I='Apply'
_H='tilt'
_G='top'
_F='*.*'
_E='All Files'
_D=True
_C=None
_B='RGBA'
_A='left'
import os,sys
def resource_path(relative_path):
	'Get absolute path to resource, works for dev and for PyInstaller.';A=relative_path
	if hasattr(sys,'_MEIPASS'):return os.path.join(sys._MEIPASS,A)
	return os.path.join(os.path.abspath('.'),A)
if sys.platform=='darwin'and os.path.exists(_J):tkdnd_folder=_J
else:tkdnd_folder=resource_path('tkdnd2.8')
os.environ['TCLLIBPATH']=tkdnd_folder
from tkinterdnd2 import TkinterDnD,DND_FILES
import tkinter as tk
from tkinter import filedialog,colorchooser,messagebox
from PIL import Image,ImageDraw,ImageTk,ImageFont,ImageColor
original_image=_C
processed_image=_C
def load_image(file_path):
	'Load the main image and show watermark options.';global original_image,processed_image
	try:original_image=Image.open(file_path);processed_image=original_image.copy();show_options_frame();dragAndDrop.config(text='Image Uploaded')
	except Exception as A:print(f"Error loading image: {A}")
def on_main_image_drop(event):
	'Drag-and-drop handler for the main image.';A=event.data.strip()
	if A.startswith('{')and A.endswith('}'):A=A[1:-1]
	load_image(A)
def upload_main_image():
	A=filedialog.askopenfilename(filetypes=((_K,_L),(_E,_F)))
	if A:load_image(A)
def show_options_frame():'Show the radio buttons and Apply button after the main image is loaded.';options_frame.pack(side=_G,fill='x',padx=20,pady=10)
def apply_changes():
	'Apply the selected operation.';global processed_image
	if original_image is _C:messagebox.showerror('Error','No image selected. Please upload an image first.');return
	A=var_option.get()
	if A==_H:
		try:B=float(tilt_entry.get())
		except ValueError:B=90
		processed_image=original_image.copy().rotate(B,expand=_D);open_preview_window(processed_image)
	elif A==_M:open_logo_window()
	elif A==_N:open_text_input_window()
	elif A==_O:open_water_over_window()
def open_logo_window():'Open a window to let the user supply a logo image (drag-and-drop or browse).';A=tk.Toplevel(window);A.title('Choose Logo for Watermark');C=tk.Label(A,text='Drag and drop your logo here or click Browse:');C.pack(padx=10,pady=10);B=tk.Label(A,text='Drop Logo Here',bg='lightgray',width=40,height=5);B.pack(padx=10,pady=10);B.drop_target_register(DND_FILES);B.dnd_bind(_P,lambda e:logo_drop_handler(e,A));D=tk.Button(A,text='Browse Logo',command=lambda:browse_logo(A));D.pack(pady=10)
def logo_drop_handler(event,logo_win):
	'Handle drop event for logo image.';A=event.data.strip()
	if A.startswith('{')and A.endswith('}'):A=A[1:-1]
	apply_logo_watermark(A);open_preview_window(processed_image);logo_win.destroy()
def browse_logo(logo_win):
	'Browse for a logo image.';A=filedialog.askopenfilename(filetypes=((_K,_L),(_E,_F)))
	if A:apply_logo_watermark(A);open_preview_window(processed_image);logo_win.destroy()
def apply_logo_watermark(logo_path):
	"Apply an image watermark using the provided logo image.\n       The logo is scaled to ~20% of the main image's width.";global processed_image;processed_image=original_image.copy()
	try:A=Image.open(logo_path).convert(_B);C,E=processed_image.size;F=.2;B=int(C*F);G=B/A.width;D=int(A.height*G);A=A.resize((B,D),Image.LANCZOS);H=C-B-10;I=E-D-10;processed_image.paste(A,(H,I),A)
	except Exception as J:print(f"Error applying logo watermark: {J}")
def open_text_input_window():'Open a window prompting the user for watermark text.';A=tk.Toplevel(window);A.title('Enter Watermark Text');C=tk.Label(A,text=_Q);C.pack(padx=10,pady=10);B=tk.Entry(A,width=30);B.pack(padx=10,pady=10);B.focus_set();D=tk.Button(A,text=_I,command=lambda:text_watermark_handler(B.get(),A));D.pack(pady=10)
def text_watermark_handler(watermark_text,text_win):
	'Apply text watermark using the provided text.';A=watermark_text
	if A.strip()=='':A='Watermark'
	apply_text_watermark(A);open_preview_window(processed_image);text_win.destroy()
def apply_text_watermark(watermark_text):
	'Apply a text watermark responsively based on main image size.\n       The font size is set to ~15% of main image width.\n       The user is prompted to choose a watermark color.';C=watermark_text;global processed_image;processed_image=original_image.copy();D=ImageDraw.Draw(processed_image);E,F=processed_image.size;J=max(int(F*.04),int(E*.04));K=resource_path('fonts/boom.ttf')
	try:A=ImageFont.truetype(K,J)
	except Exception as L:print('Using default font; true type font not found.',L);A=ImageFont.load_default()
	G=colorchooser.askcolor(title=_R);M=G[1]if G[1]is not _C else'white';N,O,P,Q=D.textbbox((0,0),C,font=A);R=P-N;S=Q-O;H=E-R-25;I=F-S-25;B=2
	for T in range(-B,B+1):
		for U in range(-B,B+1):D.text((H+T,I+U),C,font=A,fill='black')
	D.text((H,I),C,font=A,fill=M)
def open_water_over_window():
	'Open a window prompting the user to enter watermark text, choose a color, and enter a tilt angle.';A=tk.Toplevel(window);A.title('Water Over Image Options');F=tk.Label(A,text=_Q);F.pack(padx=10,pady=5);C=tk.Entry(A,width=30);C.insert(0,'SAMPLE WATERMARK');C.pack(padx=10,pady=5);G=tk.Label(A,text='Choose watermark color:');G.pack(padx=10,pady=5);B=['#FFFFFF']
	def H():
		A=colorchooser.askcolor(title=_R)
		if A[1]:B[0]=A[1];E.config(bg=B[0])
	E=tk.Button(A,text='Choose Color',command=H,bg=B[0]);E.pack(padx=10,pady=5);I=tk.Label(A,text='Enter tilt angle:');I.pack(padx=10,pady=5);D=tk.Entry(A,width=10);D.insert(0,'0');D.pack(padx=10,pady=5);J=tk.Button(A,text=_I,command=lambda:water_over_handler(C.get(),D.get(),B[0],A));J.pack(pady=10)
def water_over_handler(text_str,angle_str,chosen_color,water_over_win):
	'Handle the water-over prompt inputs and apply the tiled watermark.'
	try:A=float(angle_str)
	except ValueError:A=0
	apply_water_over(text_str,chosen_color,A);open_preview_window(processed_image);water_over_win.destroy()
def apply_water_over(text,color,angle):
	'Apply a tiled text watermark over the entire image with low opacity.';global processed_image;B=original_image.convert(_B);C=Image.new(_B,B.size,(0,0,0,0));E,F=B.size;H=max(int(F*.025),int(E*.025));I=resource_path('fonts/medium.ttf')
	try:D=ImageFont.truetype(I,H)
	except Exception as J:print('Using default font for water-over; true type font not found.',J);D=ImageFont.load_default()
	K=ImageDraw.Draw(C);L,M,N,O=K.textbbox((0,0),text,font=D);P=int(N-L);Q=int(O-M+10);G=Image.new(_B,(P,Q),(0,0,0,0));R=ImageDraw.Draw(G);S=ImageColor.getrgb(color);R.text((0,0),text,font=D,fill=S+(170,));A=G.rotate(angle,expand=_D);T=150;U=150;V=A.width+T;W=A.height+U
	for X in range(0,E,V):
		for Y in range(0,F,W):C.paste(A,(X,Y),A)
	processed_image=Image.alpha_composite(B,C)
def open_preview_window(full_res_image):'Open a preview window showing a scaled-down version of the processed image.';B=full_res_image;C=B.copy();C.thumbnail((600,600));A=tk.Toplevel(window);A.title('Preview');D=ImageTk.PhotoImage(C);E=tk.Label(A,image=D);E.pack(padx=10,pady=10);E.image=D;F=tk.Button(A,text='Save Image',command=lambda:save_image(B));F.pack(pady=5)
def save_image(pil_image):
	'Prompt the user to save the full-resolution processed image.';A=filedialog.asksaveasfilename(defaultextension='.png',filetypes=[('PNG Files','*.png'),('JPEG Files','*.jpg;*.jpeg'),(_E,_F)])
	if A:
		try:pil_image.save(A);print(f"Image saved to {A}")
		except Exception as B:print(f"Error saving image: {B}")
window=TkinterDnD.Tk()
window.title(_S)
window.minsize(600,400)
top_frame=tk.Frame(window,bg='white')
top_frame.pack(side=_G,fill='both',expand=_D,padx=20,pady=20)
width,height=500,250
corner_radius=40
bg_color=240,128,128
bg_img=Image.new(_B,(width,height),(0,0,0,0))
draw_bg=ImageDraw.Draw(bg_img)
draw_bg.rounded_rectangle((0,0,width,height),radius=corner_radius,fill=bg_color)
rounded_bg=ImageTk.PhotoImage(bg_img)
dragAndDrop=tk.Label(top_frame,image=rounded_bg,text=_S,compound='center',fg='black',font=('Times New Roman',48,'bold'))
dragAndDrop.pack(expand=_D,fill='both')
dragAndDrop.bg_image=rounded_bg
dragAndDrop.drop_target_register(DND_FILES)
dragAndDrop.dnd_bind(_P,on_main_image_drop)
bottom_frame=tk.Frame(window)
bottom_frame.pack(side='bottom',fill='x',padx=20,pady=10)
upload_btn=tk.Button(bottom_frame,text='Upload Main Image',command=upload_main_image)
upload_btn.pack(side=_G,pady=20)
options_frame=tk.Frame(bottom_frame)
var_option=tk.StringVar(value=_H)
radio_tilt=tk.Radiobutton(options_frame,text='Tilt Image ↺',variable=var_option,value=_H)
radio_tilt.pack(side=_A,padx=5)
tilt_label=tk.Label(options_frame,text='Angle:')
tilt_label.pack(side=_A,padx=(5,0))
tilt_entry=tk.Entry(options_frame,width=5)
tilt_entry.insert(0,'90')
tilt_entry.pack(side=_A)
radio_img_wm=tk.Radiobutton(options_frame,text='Add Image Watermark',variable=var_option,value=_M)
radio_img_wm.pack(side=_A,padx=5)
radio_text_wm=tk.Radiobutton(options_frame,text='Add Text Watermark',variable=var_option,value=_N)
radio_text_wm.pack(side=_A,padx=5)
radio_water_over=tk.Radiobutton(options_frame,text='WaterMark all over',variable=var_option,value=_O)
radio_water_over.pack(side=_A,padx=5)
apply_btn=tk.Button(options_frame,text=_I,command=apply_changes)
apply_btn.pack(side=_A,padx=10)
options_frame.pack()
window.mainloop()