"""Background detection and explicit video-assisted review of interval proposals."""
from __future__ import annotations
import csv
import json
import queue
import threading
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import core
from movement_detector import Settings, detect


def calibration_signature(project):
    return json.dumps([project.video,project.calibration['video_points'],project.known_cm])


class MotionReview:
    def __init__(self,app):
        self.app=app;self.project=app.project;self.root=tk.Toplevel(app.root)
        self.root.title('Suggest and verify movement intervals');self.root.geometry('1240x860')
        self.root.minsize(1040,740)
        self.root.protocol('WM_DELETE_WINDOW',self.close)
        self.running=False;self.cancel_event=None;self.poll_job=None;self.play_job=None;self.cap=None
        self.messages=queue.Queue();self.result=None;self.proposals=[];self.signature=None;self.t=0
        self.vars={};self.snapshot_video=self.project.video;self.duration=self.project.info['last_frame_s']
        ttk.Label(self.root,text='Automatic suggestions only. Review the video, edit boundaries, and verify each retained interval before using it.',
                  wraplength=1190,padding=8).pack(fill='x')
        basic=ttk.Frame(self.root);basic.pack(fill='x',padx=8,pady=3)
        fields=[('Scan start (s)','scan_start_s','0',10),('End (blank = full)','scan_end_s','',10),
                ('Min speed (cm/s)','speed_cm_s','0.5',7),('Min bout (s)','min_bout_s','0.5',7)]
        for label,key,value,width in fields:
            ttk.Label(basic,text=label).pack(side='left',padx=(5,2));var=tk.StringVar(value=value);self.vars[key]=var
            ttk.Entry(basic,textvariable=var,width=width).pack(side='left',padx=3)
        self.advanced=ttk.Frame(self.root)
        for label,key,value in [('Min excursion (cm)','min_excursion_cm','0.3'),('Merge pause (s)','merge_gap_s','0.3'),
                                ('Dark threshold','dark_threshold','100'),('Above rod (cm)','above_cm','4'),
                                ('Below rod (cm)','below_cm','8'),('Samples/s','sample_hz','8')]:
            ttk.Label(self.advanced,text=label).pack(side='left',padx=(4,2));var=tk.StringVar(value=value);self.vars[key]=var
            ttk.Entry(self.advanced,textvariable=var,width=5).pack(side='left',padx=2)
        self.below_only=tk.BooleanVar(value=True)
        ttk.Checkbutton(self.advanced,text='Hanging below rod',variable=self.below_only).pack(side='left',padx=5)
        bar=ttk.Frame(self.root);bar.pack(fill='x',padx=8,pady=5)
        self.scan_button=ttk.Button(bar,text='Detect movement proposals',command=self.run);self.scan_button.pack(side='left',padx=3)
        self.cancel_button=ttk.Button(bar,text='Cancel scan',command=self.cancel_scan,state='disabled');self.cancel_button.pack(side='left',padx=3)
        ttk.Button(bar,text='Advanced settings',command=self.toggle_advanced).pack(side='left',padx=3)
        self.progress=tk.DoubleVar(value=0);ttk.Progressbar(bar,variable=self.progress,maximum=100).pack(side='left',fill='x',expand=True,padx=8)
        self.message=tk.StringVar(value='Not scanned yet. Click Detect movement proposals to fill the table, then select and review each interval.')
        ttk.Label(self.root,textvariable=self.message,wraplength=1190,padding=(10,3)).pack(fill='x')
        panes=ttk.Panedwindow(self.root,orient='horizontal');panes.pack(fill='both',expand=True,padx=8,pady=7)
        left=ttk.Frame(panes);right=ttk.Frame(panes);panes.add(left,weight=1);panes.add(right,weight=2)
        holder=ttk.Frame(left);holder.pack(fill='both',expand=True)
        self.table=ttk.Treeview(holder,columns=('n','start','end','time','verified'),show='headings',selectmode='browse',height=10)
        for key,label,width in [('n','#',35),('start','Start (s)',85),('end','End (s)',85),('time','Time (s)',70),('verified','Review',95)]:
            self.table.heading(key,text=label);self.table.column(key,width=width,stretch=True)
        self.table.pack(side='left',fill='both',expand=True)
        scroll=ttk.Scrollbar(holder,orient='vertical',command=self.table.yview);scroll.pack(side='right',fill='y');self.table.configure(yscrollcommand=scroll.set)
        self.table.bind('<<TreeviewSelect>>',self.select)
        editor=ttk.Frame(left);editor.pack(fill='x',pady=5)
        self.start=tk.StringVar();self.end=tk.StringVar();self.editor_loading=False
        self.start.trace_add('write',self.fields_changed);self.end.trace_add('write',self.fields_changed)
        ttk.Label(editor,text='Start:').pack(side='left');ttk.Entry(editor,textvariable=self.start,width=12).pack(side='left',padx=3)
        ttk.Label(editor,text='End:').pack(side='left');ttk.Entry(editor,textvariable=self.end,width=12).pack(side='left',padx=3)
        edits=ttk.Frame(left);edits.pack(fill='x',pady=3)
        for label,command in [('Update selected',self.edit),('Add',self.add),('Remove',self.remove)]:
            ttk.Button(edits,text=label,command=command).pack(side='left',padx=2)
        ttk.Button(left,text='Mark selected interval verified',command=self.verify).pack(fill='x',pady=6)
        self.review_count=tk.StringVar(value='No proposals yet.');ttk.Label(left,textvariable=self.review_count,wraplength=370).pack(fill='x',pady=3)
        ttk.Label(right,text='Video review: check both boundaries and the behavior between them.').pack(anchor='w')
        self.preview=type(app.preview)(right,overlay=self.overlay);self.preview.pack(fill='both',expand=True)
        controls=ttk.Frame(right);controls.pack(fill='x',pady=5)
        self.time_var=tk.StringVar(value='0');ttk.Entry(controls,textvariable=self.time_var,width=12).pack(side='left',padx=2)
        for label,callback in [('Go',self.seek_typed),('− frame',lambda:self.step(-1)),('+ frame',lambda:self.step(1)),
                               ('Play interval',self.play_interval),('Pause',self.pause)]:
            ttk.Button(controls,text=label,command=callback).pack(side='left',padx=2)
        checks=ttk.Frame(right);checks.pack(fill='x',pady=3)
        for label,callback in [('Check start',lambda:self.check_boundary('start')),('Check end',lambda:self.check_boundary('end')),
                               ('Set start here',lambda:self.start.set(core.format_time(self.t))),('Set end here',lambda:self.end.set(core.format_time(self.t)))]:
            ttk.Button(checks,text=label,command=callback).pack(side='left',padx=2)
        bottom=ttk.Frame(self.root);bottom.pack(fill='x',padx=8,pady=8)
        ttk.Button(bottom,text='Save detection trace CSV',command=self.save_trace).pack(side='left',padx=3)
        self.use_button=ttk.Button(bottom,text='Use verified intervals (replace editor)',command=self.accept,state='disabled')
        self.use_button.pack(side='right',padx=3)
        ttk.Label(self.root,text='Green/red marker: detected/excluded body candidate. Detection does not measure the head endpoint distance. '
                  'Your existing manual time entry and full video-checking controls remain available on step 2.',
                  wraplength=1190,padding=(10,0,10,8)).pack(fill='x')
        self.seek(0)

    def toggle_advanced(self):
        if self.advanced.winfo_manager():self.advanced.pack_forget()
        else:self.advanced.pack(fill='x',padx=8,pady=3,after=self.root.winfo_children()[1])

    def settings(self):
        kwargs={key:float(var.get()) for key,var in self.vars.items() if key!='scan_end_s'}
        kwargs['dark_threshold']=int(kwargs['dark_threshold'])
        kwargs['scan_end_s']=float(self.vars['scan_end_s'].get()) if self.vars['scan_end_s'].get().strip() else self.duration
        kwargs['require_below_rod']=self.below_only.get()
        settings=Settings(**kwargs).validate()
        if settings.scan_end_s>self.duration+.001:raise ValueError('Scan end exceeds the last video frame.')
        if settings.scan_start_s>=self.duration:raise ValueError('Scan start exceeds the video.')
        return settings

    def run(self):
        if self.running:return
        if self.app.project is not self.project:return self.app.error('The current recording has changed. Reopen detection.')
        try:
            if not self.project.calibration['confirmed']:raise ValueError('Confirm calibration before detecting movement.')
            settings=self.settings()
        except Exception as exc:return self.app.error(exc)
        self.pause();self.signature=calibration_signature(self.project);self.cancel_event=threading.Event()
        self.running=True;self.result=None;self.proposals=[];self.refresh();self.progress.set(0)
        self.scan_button.configure(state='disabled');self.cancel_button.configure(state='normal')
        self.message.set('Scanning in the background. You can cancel; the existing interval editor is unchanged.')
        video=self.project.video;rod=[list(p) for p in self.project.calibration['video_points']];known=self.project.known_cm
        def worker():
            try:
                result=detect(video,rod,known,settings,self.cancel_event,
                    lambda fraction,t:self.messages.put(('progress',(fraction,t))))
                self.messages.put(('result',result))
            except Exception as exc:self.messages.put(('error',str(exc)))
        threading.Thread(target=worker,daemon=True,name='WireWalk movement scan').start()
        self.poll_job=self.root.after(80,self.poll)

    def poll(self):
        self.poll_job=None
        try:
            while True:
                kind,value=self.messages.get_nowait()
                if kind=='progress':
                    fraction,t=value;self.progress.set(fraction*100);self.message.set(f'Scanning: {fraction:.0%}; video time {t:.1f} s.')
                elif kind=='error':
                    self.finished();self.message.set('Detection failed. Manual time entry remains available.');self.app.error(value)
                elif kind=='result':
                    self.finished()
                    if value['cancelled']:self.message.set('Scan cancelled. No movement intervals were changed.');continue
                    self.result=value;self.proposals=[dict(start=a,end=b,verified=False) for a,b in value['windows']]
                    self.progress.set(100);self.refresh()
                    valid=value['sample_counts'].get('detected',0);count=len(value['samples'])
                    self.message.set(f'{len(self.proposals)} proposals; body detected in {valid}/{count} sampled frames. '
                        'Review all retained intervals. Edit, remove, or add intervals as needed.' if self.proposals else
                        'No proposals found. Adjust the settings or use manual time entry; no existing intervals were changed.')
        except queue.Empty:pass
        if self.running:self.poll_job=self.root.after(80,self.poll)

    def finished(self):
        self.running=False;self.scan_button.configure(state='normal');self.cancel_button.configure(state='disabled')

    def cancel_scan(self):
        if self.cancel_event:self.cancel_event.set();self.message.set('Cancelling scan…')

    def selected(self):
        values=self.table.selection()
        return int(values[0]) if values else None

    def refresh(self,index=None):
        self.table.delete(*self.table.get_children())
        for i,row in enumerate(self.proposals):
            self.table.insert('','end',iid=str(i),values=(i+1,core.format_time(row['start']),core.format_time(row['end']),
                core.format_time(row['end']-row['start']),'verified' if row['verified'] else 'needs review'))
        count=sum(r['verified'] for r in self.proposals)
        self.review_count.set(f'{count}/{len(self.proposals)} intervals verified. Changes are not applied to the main editor yet.')
        self.use_button.configure(state='normal' if self.proposals and count==len(self.proposals) and not self.running else 'disabled')
        if index is not None and self.proposals:self.table.selection_set(str(min(index,len(self.proposals)-1)))

    def select(self,event=None):
        index=self.selected()
        if index is None:return
        row=self.proposals[index];self.editor_loading=True
        self.start.set(core.format_time(row['start']));self.end.set(core.format_time(row['end']));self.editor_loading=False
        self.seek(max(0,row['start']-.25))

    def fields_changed(self,*args):
        if self.editor_loading:return
        index=self.selected()
        if index is not None:
            self.proposals[index]['verified']=False;self.table.set(str(index),'verified','needs review')
            self.use_button.configure(state='disabled')
            count=sum(r['verified'] for r in self.proposals)
            self.review_count.set(f'{count}/{len(self.proposals)} verified. Apply or verify the edited boundaries before using proposals.')

    def edit(self):
        if self.running:return self.app.error('Finish or cancel the scan before editing proposals.')
        index=self.selected()
        if index is None:return self.app.error('Select a proposed interval first.')
        try:
            a,b=core.parse_time(self.start.get()),core.parse_time(self.end.get())
            windows=[(r['start'],r['end']) if i!=index else (a,b) for i,r in enumerate(self.proposals)]
            core.validate_windows(windows,self.duration)
            self.proposals[index]=dict(start=a,end=b,verified=False);self.refresh(index);return True
        except Exception as exc:self.app.error(exc);return False

    def add(self):
        if self.running:return self.app.error('Finish or cancel the scan before adding proposals.')
        try:
            a,b=core.parse_time(self.start.get()),core.parse_time(self.end.get())
            core.validate_windows([(r['start'],r['end']) for r in self.proposals]+[(a,b)],self.duration)
            self.proposals.append(dict(start=a,end=b,verified=False));self.refresh(len(self.proposals)-1)
        except Exception as exc:self.app.error(exc)

    def remove(self):
        if self.running:return self.app.error('Finish or cancel the scan before removing proposals.')
        index=self.selected()
        if index is not None:self.proposals.pop(index);self.refresh(index)

    def verify(self):
        if self.running:return self.app.error('Wait for the scan to finish before verifying proposals.')
        index=self.selected()
        if index is None:return self.app.error('Select an interval to verify after checking the video.')
        if not self.edit():return
        self.proposals[index]['verified']=True;self.refresh(index)

    def check_boundary(self,role):
        index=self.selected()
        if index is not None:
            try:self.seek(core.parse_time(self.start.get() if role=='start' else self.end.get()))
            except Exception as exc:self.app.error(exc)

    def seek_typed(self):
        try:self.seek(core.parse_time(self.time_var.get()))
        except Exception as exc:self.app.error(exc)

    def seek(self,t):
        self.pause()
        try:
            image,self.t=core.read_frame(self.snapshot_video,max(0,min(self.duration,t)))
            self.preview.set_image(image);self.time_var.set(core.format_time(self.t))
        except Exception as exc:self.app.error(exc)

    def step(self,direction):self.seek(self.t+direction/self.project.info['fps'])

    def play_interval(self):
        import cv2
        index=self.selected()
        if index is None:return self.app.error('Select an interval to play.')
        try:
            a,b=core.parse_time(self.start.get()),core.parse_time(self.end.get())
            core.validate_windows([(a,b)],self.duration)
        except Exception as exc:return self.app.error(exc)
        self.pause();self.play_end=min(self.duration,b+.3)
        self.cap=cv2.VideoCapture(self.snapshot_video);self.cap.set(cv2.CAP_PROP_POS_MSEC,max(0,a-.3)*1000)
        self.tick()

    def tick(self):
        import cv2
        if self.cap is None:return
        ok,image=self.cap.read()
        if not ok:return self.pause()
        self.t=self.cap.get(cv2.CAP_PROP_POS_MSEC)/1000
        self.preview.set_image(image);self.time_var.set(core.format_time(self.t))
        if self.t>=self.play_end:return self.pause()
        self.play_job=self.root.after(max(1,round(1000/self.project.info['fps'])),self.tick)

    def pause(self):
        if self.play_job is not None:self.root.after_cancel(self.play_job);self.play_job=None
        if self.cap is not None:self.cap.release();self.cap=None

    def overlay(self,view):
        view.rod(self.project.calibration['video_points'])
        if not self.result or not self.result['samples']:return
        row=min(self.result['samples'],key=lambda r:abs(r['t']-self.t))
        if abs(row['t']-self.t)>.2 or row.get('body_x_px') is None:return
        x,y=view.point((row['body_x_px'],row['body_y_px']))
        color='#37ed98' if row['valid'] else '#ff5b5b'
        view.canvas.create_oval(x-8,y-8,x+8,y+8,outline=color,width=2)
        view.canvas.create_rectangle(3,3,340,28,fill='#18222c',outline='')
        view.canvas.create_text(10,16,text=f'Body sample: {row["status"]}',fill=color,anchor='w')

    def accept(self):
        if self.running:return self.app.error('Wait for the scan to finish or cancel it.')
        if not self.result or not self.proposals or not all(r['verified'] for r in self.proposals):
            return self.app.error('Verify every retained interval before applying proposals.')
        if self.app.project is not self.project or calibration_signature(self.project)!=self.signature or not self.project.calibration['confirmed']:
            return self.app.error('The video or calibration changed. Detect movement again before using these proposals.')
        try:
            windows=core.validate_windows([(r['start'],r['end']) for r in self.proposals],self.duration)
            metadata={key:self.result[key] for key in ('method','settings','sample_counts')}
            metadata.update(verified=True,edited_after_verification=False,verified_windows=windows,
                            initial_proposal_count=len(self.result['windows']),
                            proposal_edits_made=windows!=self.result['windows'])
            self.app.use_detected_intervals(windows,metadata)
            self.close()
        except Exception as exc:self.app.error(exc)

    def save_trace(self):
        if not self.result or not self.result['samples']:return self.app.error('Run detection first.')
        path=filedialog.asksaveasfilename(title='Save sampled motion diagnostics',defaultextension='.csv',
                                        initialfile=self.project.recording_id+'_motion_trace.csv')
        if path:
            try:
                with open(path,'w',encoding='utf-8-sig',newline='') as f:
                    writer=csv.DictWriter(f,fieldnames=list(self.result['samples'][0]));writer.writeheader();writer.writerows(self.result['samples'])
            except Exception as exc:self.app.error(exc)

    def close(self):
        self.cancel_scan();self.pause()
        if self.poll_job is not None:self.root.after_cancel(self.poll_job);self.poll_job=None
        self.root.destroy()
        if getattr(self.app,'motion_review',None) is self:self.app.motion_review=None
