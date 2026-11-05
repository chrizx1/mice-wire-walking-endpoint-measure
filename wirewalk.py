"""WireWalk Studio: integrated screenshot calibration and endpoint measurement."""
from __future__ import annotations
import argparse
import json
import math
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import core


class ImageView(ttk.Frame):
    """Zoomable image with mouse clicks reported in original image pixels."""
    def __init__(self,parent,on_click=None,overlay=None):
        super().__init__(parent)
        self.image=None;self.scale=1;self.ox=0;self.oy=0;self.fitted=False
        self.on_click=on_click;self.overlay=overlay;self.drag_origin=None
        bar=ttk.Frame(self);bar.pack(fill='x')
        for label,command in [('Fit',self.fit),('+',lambda:self.zoom(1.4)),('−',lambda:self.zoom(1/1.4))]:
            ttk.Button(bar,text=label,width=4,command=command).pack(side='left',padx=1)
        ttk.Label(bar,text='Wheel: zoom • right-drag: pan').pack(side='left',padx=5)
        self.canvas=tk.Canvas(self,bg='#18222c',highlightthickness=0,width=440,height=320)
        self.canvas.pack(fill='both',expand=True)
        self.canvas.bind('<Configure>',self.resize)
        self.canvas.bind('<Button-1>',self.click)
        self.canvas.bind('<MouseWheel>',lambda e:self.zoom(1.25 if e.delta>0 else 1/1.25,e.x,e.y))
        self.canvas.bind('<Button-4>',lambda e:self.zoom(1.25,e.x,e.y))
        self.canvas.bind('<Button-5>',lambda e:self.zoom(1/1.25,e.x,e.y))
        self.canvas.bind('<Button-3>',lambda e:setattr(self,'drag_origin',(e.x,e.y,self.ox,self.oy)))
        self.canvas.bind('<B3-Motion>',self.pan)

    def set_image(self,image):
        from PIL import Image
        import cv2
        self.image=Image.fromarray(cv2.cvtColor(image,cv2.COLOR_BGR2RGB)) if image is not None else None
        self.fitted=False;self.fit()

    def resize(self,event=None):
        if not self.fitted:self.fit()
        else:self.draw()

    def fit(self):
        if self.image is None:return self.draw()
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        if w<5 or h<5:return
        self.scale=min(w/self.image.width,h/self.image.height)*.98
        self.ox=(w-self.image.width*self.scale)/2;self.oy=(h-self.image.height*self.scale)/2
        self.fitted=True;self.draw()

    def zoom(self,factor,x=None,y=None):
        if self.image is None:return
        x=self.canvas.winfo_width()/2 if x is None else x
        y=self.canvas.winfo_height()/2 if y is None else y
        minimum=min(self.canvas.winfo_width()/self.image.width,self.canvas.winfo_height()/self.image.height)/2
        new=max(minimum,min(self.scale*factor,20))
        ratio=new/self.scale;self.ox=x-(x-self.ox)*ratio;self.oy=y-(y-self.oy)*ratio
        self.scale=new;self.draw()

    def pan(self,event):
        if self.drag_origin is None:return
        x,y,ox,oy=self.drag_origin;self.ox=ox+event.x-x;self.oy=oy+event.y-y;self.draw()

    def point(self,xy):return self.ox+xy[0]*self.scale,self.oy+xy[1]*self.scale

    def click(self,event):
        if self.image is None:return
        x,y=(event.x-self.ox)/self.scale,(event.y-self.oy)/self.scale
        if 0<=x<self.image.width and 0<=y<self.image.height and self.on_click:self.on_click(x,y)

    def draw(self):
        from PIL import Image,ImageTk
        self.canvas.delete('all')
        if self.image is None:
            self.canvas.create_text(20,25,text='No image loaded',fill='#b7c8d7',anchor='w');return
        w,h=self.canvas.winfo_width(),self.canvas.winfo_height()
        if w<5 or h<5:return
        view=self.image.transform((w,h),Image.Transform.AFFINE,
              (1/self.scale,0,-self.ox/self.scale,0,1/self.scale,-self.oy/self.scale),
              Image.Resampling.BILINEAR,fillcolor=(24,34,44))
        self.photo=ImageTk.PhotoImage(view)
        self.canvas.create_image(0,0,image=self.photo,anchor='nw')
        if self.overlay:self.overlay(self)

    def rod(self,points,color='#37ed98'):
        for i,xy in enumerate(points):
            x,y=self.point(xy);self.canvas.create_oval(x-5,y-5,x+5,y+5,outline=color,width=2)
            self.canvas.create_text(x+9,y-10,text='AB'[i] if i<2 else '',fill=color,anchor='w')
        if len(points)==2:self.canvas.create_line(*self.point(points[0]),*self.point(points[1]),fill=color,width=2)

    def head(self,xy,rod,known_cm):
        x,y=self.point(xy)
        self.canvas.create_line(x-8,y,x+8,y,fill='#00e6ff',width=2)
        self.canvas.create_line(x,y-8,x,y+8,fill='#00e6ff',width=2)
        if len(rod)==2:
            coordinate,q=core.rod_projection(xy,*rod,known_cm);qx,qy=self.point(q)
            self.canvas.create_line(x,y,qx,qy,fill='#ffbd59',dash=(3,3))
            self.canvas.create_oval(qx-5,qy-5,qx+5,qy+5,fill='#ffbd59')
            self.canvas.create_rectangle(3,3,320,28,fill='#18222c',outline='')
            self.canvas.create_text(10,16,text=f'Position along rod from A: {coordinate:.3f} cm',fill='white',anchor='w')


class Studio:
    def __init__(self,root,video=None):
        self.root=root;self.project=None;self.project_path=None;self.dirty=False;self.loading=False
        self.source_image=None;self.reference_image=None;self.measure_frames={};self.reference_actual=0
        self.wi=0;self.source_select=False;self.video_select=False
        self.play_cap=None;self.play_job=None;self.seek_job=None;self.preview_time=0;self.setting_slider=False
        self.motion_review=None
        root.title('WireWalk Studio');root.geometry('1400x930');root.minsize(1100,760)
        toolbar=ttk.Frame(root);toolbar.pack(fill='x',padx=10,pady=8)
        for label,callback in [('New video',self.open_video),('Open project',self.load_project),
                               ('Save project',self.save_project),('Export distance + speed',self.export),
                               ('Summarize exports',self.summarize_exports)]:
            ttk.Button(toolbar,text=label,command=callback).pack(side='left',padx=3)
        self.title=tk.StringVar(value='Open a video to begin. Any filename is accepted.')
        ttk.Label(root,textvariable=self.title).pack(fill='x',padx=14)
        self.tabs=ttk.Notebook(root);self.tabs.pack(fill='both',expand=True,padx=10,pady=7)
        self.cal_tab=ttk.Frame(self.tabs);self.time_tab=ttk.Frame(self.tabs);self.measure_tab=ttk.Frame(self.tabs)
        self.tabs.add(self.cal_tab,text='1  Calibrate');self.tabs.add(self.time_tab,text='2  Movement times');self.tabs.add(self.measure_tab,text='3  Measure endpoints')
        self.tabs.bind('<<NotebookTabChanged>>',lambda e:self.pause())
        self.status=tk.StringVar(value='Your video and images stay on your computer. Save a project to preserve the full workflow.')
        ttk.Label(root,textvariable=self.status,wraplength=1350).pack(fill='x',padx=14,pady=(2,9))
        self.build_calibration();self.build_times();self.build_measurements()
        root.protocol('WM_DELETE_WINDOW',self.close)
        if video:root.after(100,lambda:self.open_video(video))

    def error(self,error):messagebox.showerror('WireWalk Studio',str(error))

    def changed(self):
        if self.project is not None and not self.loading:self.dirty=True

    def build_calibration(self):
        meta=ttk.Frame(self.cal_tab);meta.pack(fill='x',padx=8,pady=8)
        self.meta={}
        for label,key,width in [('Recording ID','recording_id',25),('Animal (optional)','animal',15),('Date YYYY-MM-DD','date',13),('Trial (optional)','run',6)]:
            ttk.Label(meta,text=label).pack(side='left',padx=(8,3));var=tk.StringVar();self.meta[key]=var
            ttk.Entry(meta,textvariable=var,width=width).pack(side='left');var.trace_add('write',lambda *args:self.changed())
        bar=ttk.Frame(self.cal_tab);bar.pack(fill='x',padx=8,pady=4)
        ttk.Label(bar,text='Known rod span:').pack(side='left')
        self.length=tk.StringVar(value='24');self.unit=tk.StringVar(value='inches')
        ttk.Entry(bar,textvariable=self.length,width=9).pack(side='left',padx=4)
        ttk.Combobox(bar,textvariable=self.unit,values=list(core.UNIT_CM),state='readonly',width=8).pack(side='left')
        self.length.trace_add('write',self.length_changed);self.unit.trace_add('write',self.length_changed)
        ttk.Label(bar,text='Reference video time:').pack(side='left',padx=(14,3))
        self.reference_time=tk.StringVar(value='0');ttk.Entry(bar,textvariable=self.reference_time,width=10).pack(side='left')
        ttk.Button(bar,text='Show reference frame',command=self.show_reference).pack(side='left',padx=5)
        bar=ttk.Frame(self.cal_tab);bar.pack(fill='x',padx=8,pady=4)
        for label,callback in [('Load screenshot',self.load_screenshot),('Use video frame as screenshot',self.use_video_frame),
                               ('Select screenshot endpoints',self.select_source),('Transfer to video',self.transfer)]:
            ttk.Button(bar,text=label,command=callback).pack(side='left',padx=3)
        panes=ttk.Panedwindow(self.cal_tab,orient='horizontal');panes.pack(fill='both',expand=True,padx=7,pady=6)
        left=ttk.Frame(panes);right=ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=1)
        ttk.Label(left,text='Calibration screenshot: click the two endpoints of the known span.').pack(anchor='w')
        ttk.Label(right,text='Original video: verify the transferred endpoints.').pack(anchor='w')
        self.source_view=ImageView(left,self.click_source,lambda v:v.rod(self.project.calibration['source_points']) if self.project else None)
        self.source_view.pack(fill='both',expand=True)
        self.video_view=ImageView(right,self.click_video_rod,lambda v:v.rod(self.project.calibration['video_points']) if self.project else None)
        self.video_view.pack(fill='both',expand=True)
        self.cal_text=tk.StringVar(value='Load a screenshot or use a video frame. Click both endpoints of the physical span you measured.')
        ttk.Label(self.cal_tab,textvariable=self.cal_text,wraplength=1310).pack(fill='x',padx=12,pady=6)
        bar=ttk.Frame(self.cal_tab);bar.pack(fill='x',padx=8,pady=7)
        ttk.Button(bar,text='Correct endpoints in video',command=self.select_video_rod).pack(side='left',padx=3)
        ttk.Button(bar,text='Confirm calibration',command=self.confirm_calibration).pack(side='left',padx=3)
        ttk.Button(bar,text='Next: movement times',command=lambda:self.tabs.select(self.time_tab)).pack(side='right',padx=3)
        ttk.Label(self.cal_tab,text='Select the actual known span: rod tips and support intersections can represent different lengths. '
                  'The camera must stay fixed within a recording; a two-point scale assumes approximately uniform scale along the rod.',
                  wraplength=1310).pack(fill='x',padx=12,pady=(0,7))

    def build_times(self):
        panes=ttk.Panedwindow(self.time_tab,orient='horizontal');panes.pack(fill='both',expand=True,padx=8,pady=8)
        left=ttk.Frame(panes,width=420);right=ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=2)
        ttk.Label(left,text='Enter movement intervals (seconds, MM:SS, or HH:MM:SS).',wraplength=420).pack(anchor='w')
        self.times=tk.Text(left,width=45,height=8,undo=True);self.times.pack(fill='x',pady=5)
        self.times.insert('1.0','');self.times.bind('<<Modified>>',self.times_edited)
        ttk.Label(left,text='Example: 16-18, 0:22-0:29\nSeparate intervals with commas or new lines.').pack(anchor='w')
        bar=ttk.Frame(left);bar.pack(fill='x',pady=7)
        ttk.Button(bar,text='Apply intervals',command=self.apply_times).pack(side='left',padx=3)
        ttk.Button(bar,text='Import timestamp CSV',command=self.import_times).pack(side='left',padx=3)
        ttk.Button(left,text='Suggest movement intervals automatically…',command=self.detect_movements).pack(fill='x',pady=(0,5))
        self.time_table=ttk.Treeview(left,columns=('n','start','end','time','status'),show='headings',height=9)
        for key,label,width in [('n','#',35),('start','Start (s)',75),('end','End (s)',75),('time','Moving (s)',80),('status','State',120)]:
            self.time_table.heading(key,text=label);self.time_table.column(key,width=width,stretch=True)
        self.time_table.pack(fill='both',expand=True,pady=5)
        self.time_table.bind('<<TreeviewSelect>>',self.table_select)
        ttk.Label(right,text='Optional: inspect the video and mark movement boundaries here.').pack(anchor='w')
        self.preview=ImageView(right);self.preview.pack(fill='both',expand=True)
        seek=ttk.Frame(right);seek.pack(fill='x',pady=5)
        self.seek_time=tk.StringVar(value='0')
        ttk.Entry(seek,textvariable=self.seek_time,width=12).pack(side='left',padx=3)
        ttk.Button(seek,text='Go to time',command=self.seek_typed).pack(side='left')
        for label,callback in [('− frame',lambda:self.step_preview(-1)),('+ frame',lambda:self.step_preview(1)),('Play / pause',self.toggle_play)]:
            ttk.Button(seek,text=label,command=callback).pack(side='left',padx=3)
        self.slider_var=tk.DoubleVar(value=0);self.slider=ttk.Scale(right,from_=0,to=1,variable=self.slider_var,command=self.scrub)
        self.slider.pack(fill='x',pady=3)
        marks=ttk.Frame(right);marks.pack(fill='x',pady=4)
        self.mark_start=tk.StringVar(value='');self.mark_end=tk.StringVar(value='')
        ttk.Button(marks,text='Set start',command=lambda:self.mark_start.set(core.format_time(self.preview_time))).pack(side='left')
        ttk.Entry(marks,textvariable=self.mark_start,width=10).pack(side='left',padx=3)
        ttk.Button(marks,text='Set end',command=lambda:self.mark_end.set(core.format_time(self.preview_time))).pack(side='left')
        ttk.Entry(marks,textvariable=self.mark_end,width=10).pack(side='left',padx=3)
        ttk.Button(marks,text='Add interval',command=self.add_marked_interval).pack(side='left',padx=3)
        bar=ttk.Frame(self.time_tab);bar.pack(fill='x',padx=8,pady=7)
        ttk.Button(bar,text='Next: measure endpoints',command=self.go_measure).pack(side='right')
        ttk.Label(bar,text='Enter times manually, or detect proposals and verify/edit them. Video checking remains available.').pack(side='left')

    def build_measurements(self):
        bar=ttk.Frame(self.measure_tab);bar.pack(fill='x',padx=8,pady=8)
        ttk.Label(bar,text='Movement:').pack(side='left')
        self.movement_choice=ttk.Combobox(bar,state='readonly',width=45);self.movement_choice.pack(side='left',padx=8)
        self.movement_choice.bind('<<ComboboxSelected>>',self.show_movement)
        ttk.Button(bar,text='Previous',command=lambda:self.move(-1)).pack(side='left',padx=3)
        ttk.Button(bar,text='Next',command=lambda:self.move(1)).pack(side='left',padx=3)
        panes=ttk.Panedwindow(self.measure_tab,orient='horizontal');panes.pack(fill='both',expand=True,padx=8,pady=5)
        self.head_views={};self.head_labels={}
        for role in ('start','end'):
            frame=ttk.Frame(panes);panes.add(frame,weight=1)
            label=tk.StringVar(value=role.upper()+': click the head.');self.head_labels[role]=label
            ttk.Label(frame,textvariable=label).pack(anchor='w')
            view=ImageView(frame,lambda x,y,r=role:self.click_head(r,x,y),lambda v,r=role:self.head_overlay(v,r))
            view.pack(fill='both',expand=True);self.head_views[role]=view
        self.measure_text=tk.StringVar(value='Apply movement intervals, then click the same head landmark in both frames.')
        ttk.Label(self.measure_tab,textvariable=self.measure_text,font=('Segoe UI',12),wraplength=1320).pack(fill='x',padx=12,pady=8)
        bar=ttk.Frame(self.measure_tab);bar.pack(fill='x',padx=8,pady=6)
        ttk.Button(bar,text='Confirm this measurement',command=self.confirm_movement).pack(side='left',padx=3)
        ttk.Button(bar,text='Clear these head points',command=self.clear_heads).pack(side='left',padx=3)
        ttk.Button(bar,text='Export distance + speed',command=self.export).pack(side='right',padx=3)
        ttk.Label(self.measure_tab,text='Distance = absolute start-to-end separation along the rod; intermediate head motion is ignored. '
                  'Speed = that separation / annotated moving time. Cyan cross: head. Orange dot: projection onto rod.',
                  wraplength=1320).pack(fill='x',padx=12,pady=(0,8))

    def allow_replace(self):
        return not self.dirty or messagebox.askyesno('Unsaved project','Save first if needed. Continue without saving these changes?')

    def pause(self):
        if self.play_job is not None:self.root.after_cancel(self.play_job);self.play_job=None
        if self.seek_job is not None:self.root.after_cancel(self.seek_job);self.seek_job=None
        if self.play_cap is not None:self.play_cap.release();self.play_cap=None

    def open_video(self,path=None):
        if not self.allow_replace():return False
        if not path:path=filedialog.askopenfilename(title='Open video',filetypes=[('Video','*.mp4 *.mov *.avi *.mkv'),('All files','*.*')])
        if not path:return False
        try:
            info,first=core.video_info(path)
            project=core.Project(path,info)
            self.close_motion_review();self.pause();self.project=project;self.project_path=None;self.dirty=False
            self.install_project(first)
            self.tabs.select(self.cal_tab)
            self.status.set('Load a calibration screenshot, or use a video frame. Enter the known rod span and select its endpoints.')
            return True
        except Exception as exc:self.error(exc);return False

    def install_project(self,first):
        p=self.project;self.loading=True
        try:
            for key,var in self.meta.items():var.set(str(getattr(p,key)))
            self.length.set(str(p.length));self.unit.set(p.unit)
            self.reference_time.set(core.format_time(p.calibration['reference_time_s']))
            self.times.delete('1.0','end')
            self.times.insert('1.0','\n'.join(f'{core.format_time(m["start_s"])}-{core.format_time(m["end_s"])}' for m in p.movements))
            self.times.edit_modified(False)
            self.source_image=None
            screenshot=p.calibration['screenshot']
            if screenshot and Path(screenshot).is_file():self.source_image=core.image_read(screenshot)
            elif p.calibration['source_type']=='video_frame':self.source_image=core.read_frame(p.video,p.calibration.get('source_frame_time_s',p.calibration['reference_time_s']))[0]
            self.source_view.set_image(self.source_image)
            self.reference_image,self.reference_actual=core.read_frame(p.video,p.calibration['reference_time_s'])
            self.video_view.set_image(self.reference_image)
            self.slider.configure(to=p.info['last_frame_s']);self.display_preview(first,0)
            self.title.set(f'{Path(p.video).name}    {p.info["width"]} × {p.info["height"]}    Last frame: {p.info["last_frame_s"]:.3f} s')
            self.measure_frames={};self.wi=0
            self.mark_start.set('');self.mark_end.set('')
            self.source_select=False;self.video_select=False
            self.update_choices();self.update_calibration_label();self.update_measurement()
            if p.movements:self.show_movement()
        finally:self.loading=False

    def sync_metadata(self):
        if self.project is None:raise ValueError('Open a video first.')
        for key,var in self.meta.items():setattr(self.project,key,var.get().strip())
        self.project.validate_metadata();self.meta['recording_id'].set(self.project.recording_id)
        self.project.set_length(self.length.get(),self.unit.get())

    def length_changed(self,*args):
        if self.project is None or self.loading:return
        try:self.project.set_length(self.length.get(),self.unit.get())
        except ValueError:self.project.invalidate_calibration()
        self.changed();self.update_calibration_label();self.update_measurement()

    def update_calibration_label(self):
        if self.project is None:return
        p=self.project;c=p.calibration;pieces=[f'Known span: {p.known_cm:g} cm.']
        if len(c['source_points'])==2:
            pixels=math.dist(*c['source_points']);pieces.append(f'Screenshot span: {pixels:.2f} pixels.')
        if len(c['video_points'])==2:
            pixels=math.dist(*c['video_points'])
            pieces.append(f'Video span: {pixels:.2f} pixels; scale: {p.known_cm/pixels:.6f} cm/pixel.' if pixels>=1 else 'Video endpoints must be distinct.')
        pieces.append('Calibration confirmed.' if c['confirmed'] else 'Calibration needs confirmation.')
        self.cal_text.set(' '.join(pieces));self.source_view.draw();self.video_view.draw()

    def show_reference(self):
        if self.project is None:return
        try:
            t=core.parse_time(self.reference_time.get());image,actual=core.read_frame(self.project.video,t)
            self.reference_image,self.reference_actual=image,actual;self.video_view.set_image(image)
            self.project.calibration['reference_time_s']=actual;self.changed()
            return True
        except Exception as exc:self.error(exc);return False

    def load_screenshot(self,path=None):
        if self.project is None:return self.error('Open a video first.')
        if not path:path=filedialog.askopenfilename(title='Calibration screenshot',filetypes=[('Images','*.png *.jpg *.jpeg *.bmp *.tif *.tiff')])
        if not path:return
        try:
            image=core.image_read(path);self.set_source(image,'screenshot',str(Path(path).resolve()))
        except Exception as exc:self.error(exc)

    def use_video_frame(self):
        if self.project is None:return
        if not self.show_reference():return
        if self.reference_image is not None:self.set_source(self.reference_image.copy(),'video_frame','')

    def set_source(self,image,kind,path):
        self.source_image=image;c=self.project.calibration
        c.update(source_type=kind,screenshot=path,source_points=[],video_points=[],mapping={},reference_time_s=self.reference_actual,source_frame_time_s=self.reference_actual)
        self.project.invalidate_calibration();self.source_select=True;self.video_select=False
        self.source_view.set_image(image);self.changed();self.update_calibration_label();self.update_measurement()
        self.status.set('Click the two endpoints of the known span in the left screenshot. Zoom for precision.')

    def select_source(self):
        if self.source_image is None:return self.error('Load a screenshot or use a video frame first.')
        self.project.calibration['source_points']=[];self.project.calibration['video_points']=[]
        self.project.invalidate_calibration();self.source_select=True;self.video_select=False;self.changed();self.update_calibration_label();self.update_measurement()

    def click_source(self,x,y):
        if not self.source_select or self.project is None:return
        points=self.project.calibration['source_points'];points.append([x,y])
        if len(points)==2:
            if math.dist(*points)<10:
                points.pop();return self.error('The known rod endpoints are too close. Select the full known span.')
            self.source_select=False;self.status.set('Screenshot endpoints selected. Click Transfer to video, check the right image, then confirm calibration.')
        self.changed();self.update_calibration_label()

    def transfer(self):
        if self.project is None:return
        try:
            self.sync_metadata();c=self.project.calibration
            if self.source_image is None or len(c['source_points'])!=2:raise ValueError('Select both screenshot endpoints first.')
            if c['source_type']=='video_frame':
                c['video_points']=[list(p) for p in c['source_points']];c['mapping']={'method':'original video-frame coordinates'}
            else:c['video_points'],c['mapping']=core.map_screenshot(self.source_image,self.reference_image,c['source_points'])
            self.project.invalidate_calibration();self.source_select=False;self.video_select=False;self.changed();self.update_calibration_label();self.update_measurement()
            self.status.set('Transferred endpoints are a proposal. Check the green A/B points in the original video and confirm calibration.')
        except Exception as exc:self.error(exc)

    def select_video_rod(self):
        if self.project is None:return
        self.project.calibration['video_points']=[];self.project.invalidate_calibration()
        self.video_select=True;self.source_select=False;self.changed();self.update_calibration_label();self.update_measurement()
        self.status.set('Click the two endpoints of the same known physical span in the RIGHT video image.')

    def click_video_rod(self,x,y):
        if not self.video_select or self.project is None:return
        points=self.project.calibration['video_points'];points.append([x,y])
        if len(points)==2:
            if math.dist(*points)<10:points.pop();return self.error('Select the two endpoints of the known rod span.')
            self.video_select=False;self.project.calibration['mapping']={'method':'manual endpoints in video'}
            self.status.set('Video endpoints selected. Click Confirm calibration.')
        self.changed();self.update_calibration_label()

    def confirm_calibration(self):
        if self.project is None:return
        try:
            self.sync_metadata();self.project.confirm_calibration();self.changed()
            self.update_calibration_label();self.update_measurement();self.status.set('Calibration confirmed. Enter the movement timestamps in step 2.')
        except Exception as exc:self.error(exc)

    def times_edited(self,event=None):
        if self.times.edit_modified():
            self.changed();self.times.edit_modified(False)

    def update_choices(self):
        if self.project is None:return
        self.time_table.delete(*self.time_table.get_children())
        values=[]
        rows=self.project.rows()
        for i,m in enumerate(self.project.movements):
            values.append(f'{i+1}: {core.format_time(m["start_s"])}–{core.format_time(m["end_s"])} s')
            self.time_table.insert('','end',iid=str(i),values=(i+1,core.format_time(m['start_s']),core.format_time(m['end_s']),
                 core.format_time(m['end_s']-m['start_s']),'confirmed' if rows[i]['reviewed'] else
                 ('needs confirmation' if rows[i]['measured_separation_cm'] is not None else 'needs head points')))
        self.movement_choice['values']=values
        if values:self.wi=min(self.wi,len(values)-1);self.movement_choice.current(self.wi)
        else:self.movement_choice.set('');self.wi=0

    def apply_times(self):
        if self.project is None:return self.error('Open a video first.')
        try:
            windows=core.parse_intervals(self.times.get('1.0','end'),self.project.info['last_frame_s'])
            self.project.set_windows(windows);self.changed();self.update_choices();self.show_movement()
            self.status.set(f'Applied {len(windows)} movement intervals; {sum(b-a for a,b in windows):g} seconds moving.')
            return True
        except Exception as exc:self.error(exc);return False

    def import_times(self,path=None):
        if self.project is None:return self.error('Open a video first.')
        if not path:path=filedialog.askopenfilename(title='Import timestamp CSV',filetypes=[('CSV','*.csv')])
        if not path:return
        try:
            windows,skipped=core.read_interval_csv(path,self.project.video,self.project.info['last_frame_s'])
            self.project.set_windows(windows)
            self.times.delete('1.0','end');self.times.insert('1.0','\n'.join(f'{core.format_time(a)}-{core.format_time(b)}' for a,b in windows))
            self.changed();self.update_choices();self.show_movement()
            self.status.set(f'Imported {len(windows)} movement intervals. Skipped {skipped} rows for other videos.')
        except Exception as exc:self.error(exc)

    def detect_movements(self):
        if self.project is None:return self.error('Open a video first.')
        if not self.project.calibration['confirmed']:return self.error('Confirm the video rod calibration before detecting movement.')
        if self.motion_review is not None:
            self.motion_review.root.deiconify();self.motion_review.root.lift();return
        from motion_review import MotionReview
        self.pause();self.motion_review=MotionReview(self)

    def use_detected_intervals(self,windows,metadata):
        self.project.set_windows(windows)
        self.project.movement_detection=metadata
        self.times.delete('1.0','end')
        self.times.insert('1.0','\n'.join(f'{core.format_time(a)}-{core.format_time(b)}' for a,b in windows))
        self.changed();self.update_choices();self.show_movement()
        self.tabs.select(self.time_tab)
        self.status.set(f'Applied {len(windows)} user-verified movement intervals. You can still edit the times and inspect the video before measuring head endpoints.')

    def close_motion_review(self):
        if self.motion_review is not None:self.motion_review.close()

    def table_select(self,event=None):
        selected=self.time_table.selection()
        if selected and self.project is not None:
            self.wi=int(selected[0]);self.movement_choice.current(self.wi)
            self.seek_preview(self.project.movements[self.wi]['start_s'])

    def display_preview(self,image,t):
        self.preview_time=t;self.preview.set_image(image);self.seek_time.set(core.format_time(t))
        self.setting_slider=True;self.slider_var.set(t);self.setting_slider=False

    def seek_preview(self,t):
        if self.project is None:return
        self.pause()
        try:
            image,actual=core.read_frame(self.project.video,float(t));self.display_preview(image,actual)
        except Exception as exc:self.error(exc)

    def seek_typed(self):
        try:self.seek_preview(core.parse_time(self.seek_time.get()))
        except Exception as exc:self.error(exc)

    def scrub(self,value):
        if self.setting_slider or self.project is None:return
        self.pause();self.seek_job=self.root.after(80,lambda:self.seek_preview(float(value)))

    def step_preview(self,direction):
        if self.project is None:return
        t=max(0,min(self.project.info['last_frame_s'],self.preview_time+direction/self.project.info['fps']))
        self.seek_preview(t)

    def toggle_play(self):
        import cv2
        if self.project is None:return
        if self.play_cap is not None:return self.pause()
        self.pause();self.play_cap=cv2.VideoCapture(self.project.video)
        self.play_cap.set(cv2.CAP_PROP_POS_MSEC,self.preview_time*1000)
        self.play_tick()

    def play_tick(self):
        import cv2
        if self.play_cap is None:return
        ok,image=self.play_cap.read()
        if not ok:return self.pause()
        actual=self.play_cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        self.display_preview(image,actual)
        self.play_job=self.root.after(max(1,round(1000/self.project.info['fps'])),self.play_tick)

    def add_marked_interval(self):
        try:
            core.parse_time(self.mark_start.get());core.parse_time(self.mark_end.get())
            existing=self.times.get('1.0','end').strip()
            text=(existing+'\n' if existing else '')+self.mark_start.get()+'-'+self.mark_end.get()
            core.parse_intervals(text,self.project.info['last_frame_s'])
            self.times.delete('1.0','end');self.times.insert('1.0',text);self.apply_times()
        except Exception as exc:self.error(exc)

    def go_measure(self):
        if self.project is None:return
        if not self.apply_times():return
        self.tabs.select(self.measure_tab)

    def show_movement(self,event=None):
        if self.project is None or not self.project.movements:return
        self.pause();index=self.movement_choice.current()
        if index<0:index=0
        try:
            movement=self.project.movements[index];frames={}
            for role in ('start','end'):frames[role]=core.read_frame(self.project.video,movement[role+'_s'])
            self.wi=index;self.measure_frames=frames
            for role,(image,t) in frames.items():
                self.head_views[role].set_image(image)
                self.head_labels[role].set(f'{role.upper()}: requested {movement[role+"_s"]:g} s; frame {t:.3f} s. Click the head.')
            self.update_measurement()
        except Exception as exc:self.error(exc)

    def move(self,direction):
        if self.project is None or not self.project.movements:return
        self.movement_choice.current((self.wi+direction)%len(self.project.movements));self.show_movement()

    def head_overlay(self,view,role):
        if self.project is None:return
        view.rod(self.project.calibration['video_points'])
        if self.wi>=len(self.project.movements):return
        point=self.project.movements[self.wi]['points'].get(role)
        if point:view.head((point['x'],point['y']),self.project.calibration['video_points'],self.project.known_cm)

    def click_head(self,role,x,y):
        if role not in self.measure_frames:return
        try:
            self.project.mark_head(self.wi,role,x,y,self.measure_frames[role][1]);self.changed()
            self.update_measurement();self.update_choices()
        except Exception as exc:self.error(exc)

    def update_measurement(self):
        for view in self.head_views.values():view.draw()
        if self.project is None or not self.project.movements:
            self.measure_text.set('Apply movement intervals, then click the same head landmark in both frames.');return
        self.wi=min(self.wi,len(self.project.movements)-1)
        r=self.project.rows()[self.wi]
        if r['measured_separation_cm'] is None:self.measure_text.set('Click the same head landmark in the START and END images.');return
        distance=r['measured_separation_cm'];time=r['moving_time_s']
        self.measure_text.set(f'Endpoint separation: {distance:.3f} cm ({distance/2.54:.3f} inches). '
                              f'Moving time: {time:g} s. Endpoint speed: {distance/time:.3f} cm/s. '
                              +('Confirmed.' if r['reviewed'] else 'Needs confirmation.'))

    def confirm_movement(self):
        if self.project is None or not self.project.movements:return
        try:
            self.project.confirm_movement(self.wi);self.changed();self.update_measurement();self.update_choices()
            self.status.set('Measurement confirmed. Continue to the next movement, or export the recording.')
        except Exception as exc:self.error(exc)

    def clear_heads(self):
        if self.project is None or not self.project.movements:return
        self.project.movements[self.wi]['points']={};self.project.movements[self.wi]['reviewed']=False
        self.changed();self.update_measurement();self.update_choices()

    def save_project(self,path=None):
        if self.project is None:return
        try:
            self.sync_metadata()
            # Apply unsaved timestamp text so saving cannot lose the typed intervals.
            text=self.times.get('1.0','end').strip()
            if text:self.project.set_windows(core.parse_intervals(text,self.project.info['last_frame_s']))
            else:
                self.project.movements=[]
                if self.project.movement_detection:self.project.movement_detection['edited_after_verification']=True
            if not path:path=filedialog.asksaveasfilename(title='Save WireWalk project',defaultextension='.json',
                 initialfile=self.project.recording_id+'_project.json',filetypes=[('WireWalk project','*.json')])
            if not path:return
            self.project.save(path);self.project_path=Path(path);self.dirty=False;self.update_choices()
            self.status.set(f'Project saved: {path}')
        except Exception as exc:self.error(exc)

    def load_project(self,path=None):
        if not self.allow_replace():return
        if not path:path=filedialog.askopenfilename(title='Open WireWalk project',filetypes=[('Project','*.json')])
        if not path:return
        try:
            try:project=core.Project.load(path)
            except FileNotFoundError:
                replacement=filedialog.askopenfilename(title='Locate the project video',filetypes=[('Video','*.mp4 *.mov *.avi *.mkv')])
                if not replacement:return
                project=core.Project.load(path,replacement)
            self.close_motion_review();self.pause();self.project=project;self.project_path=Path(path);self.install_project(core.read_frame(project.video,0)[0]);self.dirty=False
            self.status.set('Project loaded. Calibration, movement intervals, and saved head points are restored.')
        except Exception as exc:self.error(exc)

    def export(self,directory=None):
        if self.project is None:return
        try:
            self.sync_metadata()
            if not self.apply_times():return
            if not directory:directory=filedialog.askdirectory(title='Export recording results')
            if not directory:return
            summary=self.project.export(directory);self.project_path=Path(directory)/(self.project.recording_id+'_project.json');self.dirty=False
            self.status.set(f'Exported summary, movement measurements, and project. '
                            f'{summary["confirmed_intervals"]}/{summary["movement_intervals"]} movements confirmed. '
                            'Final distance and speed are blank until every movement is confirmed.')
        except Exception as exc:self.error(exc)

    def summarize_exports(self):
        folder=filedialog.askdirectory(title='Folder containing exported endpoint CSV files')
        if not folder:return
        try:
            from summarize_exports import summarize
            output=Path(folder).resolve().parent/'combined_endpoint_summary'
            report=summarize(folder,output);messagebox.showinfo('Summary complete',report+f'\nSaved to:\n{output}')
        except Exception as exc:self.error(exc)

    def close(self):
        if self.allow_replace():self.close_motion_review();self.pause();self.root.destroy()


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--video',type=Path)
    args=parser.parse_args()
    try:
        import cv2,numpy,PIL
    except ImportError as exc:raise SystemExit(f'{exc}\nRun: python -m pip install -r requirements.txt')
    root=tk.Tk();Studio(root,args.video);root.mainloop()


if __name__=='__main__':main()
