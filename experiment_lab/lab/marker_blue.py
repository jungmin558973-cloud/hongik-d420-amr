"""Marker-bounded blue liquid detection. Pixel coordinates, not plane-calibrated mm."""
import cv2
import numpy as np
import time


class MarkerBlue:
    def __init__(self,settings):
        self.settings=settings;self.baseline=[]
        self.reference=None;self.reference_width=None;self.reference_started=None;self.reference_error='';self.reference_candidates=[]
        saved=settings.get('reference_profile')
        if isinstance(saved,list) and len(saved)==101 and np.all(np.isfinite(saved)):
            self.reference=np.array(saved,float);self.reference_width=settings.get('reference_width')
        self.detector=cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_4X4_50),cv2.aruco.DetectorParameters())

    def begin_reference(self,now=None):
        self.reference=None;self.reference_started=time.perf_counter() if now is None else now;self.reference_candidates=[];self.reference_error=''
        self.settings.pop('reference_profile',None)

    def process(self,frame,timestamp=None):
        out=frame.copy();bad={'valid':False,'confidence':0.,'displacement_px':None,'left_px':None,'center_px':None,'right_px':None,'rim_margin_px':None,'method':'marker_blue','reference_locked':self.reference is not None,'units':'image px; board and liquid are different planes'}
        corners,ids,_=self.detector.detectMarkers(frame)
        allowed=set(self.settings.get('marker_ids',range(8)))
        selected=[(q,int(i)) for q,i in zip(corners,ids.flatten() if ids is not None else []) if int(i) in allowed]
        bad['marker_ids']=[i for q,i in selected]
        if len(selected)<4:return {**bad,'reason':'Need >=4 visible board markers'},out
        centers=np.array([q[0].mean(axis=0) for q,i in selected],np.float32)
        # The cup extends below the lowest markers in this physical setup.
        # Extend the marker-defined search window downward; keep lateral restriction.
        extension=np.array([0.,(centers[:,1].max()-centers[:,1].min())*.65],np.float32)
        hull=cv2.convexHull(np.vstack((centers,centers+extension))).astype(np.int32)
        if cv2.contourArea(hull)<frame.shape[0]*frame.shape[1]*.015:return {**bad,'reason':'Marker board too small/degenerate'},out
        region=np.zeros(frame.shape[:2],np.uint8);cv2.fillConvexPoly(region,hull,255)
        hsv=cv2.cvtColor(frame,cv2.COLOR_BGR2HSV)
        mask=cv2.inRange(hsv,np.array(self.settings.get('hsv_lower',[90,80,40]),np.uint8),np.array(self.settings.get('hsv_upper',[135,255,255]),np.uint8))
        mask=cv2.bitwise_and(mask,region)
        mask=cv2.morphologyEx(mask,cv2.MORPH_OPEN,np.ones((3,3),np.uint8))
        n,labels,stats,_=cv2.connectedComponentsWithStats(mask)
        candidates=sorted(range(1,n),key=lambda i:stats[i,cv2.CC_STAT_AREA],reverse=True)
        if not candidates or stats[candidates[0],cv2.CC_STAT_AREA]<100:return {**bad,'reason':'No blue liquid inside marker board'},out
        k=candidates[0]
        if len(candidates)>1 and stats[candidates[1],cv2.CC_STAT_AREA]>.45*stats[k,cv2.CC_STAT_AREA]:return {**bad,'reason':'Multiple blue objects; narrow the board/cup view'},out
        x,y,w,h=map(int,stats[k,:4]);inset=max(2,int(w*.12));xs=[];ys=[]
        for col in range(x+inset,x+w-inset):
            rows=np.flatnonzero(labels[:,col]==k)
            if len(rows)>=3:xs.append(col);ys.append(rows[0])
        if len(xs)<max(10,w*.5):return {**bad,'reason':'Insufficient liquid surface support'},out
        xs=np.array(xs);ys=np.array(ys,dtype=float)
        # Estimate threshold crossings locally; subpixel interpolation is not accuracy calibration.
        low=np.array(self.settings.get('hsv_lower',[90,80,40]),float)
        high=np.array(self.settings.get('hsv_upper',[135,255,255]),float)
        for j,(col,row) in enumerate(zip(xs,ys.astype(int))):
            if row<=0:continue
            samples=hsv[row-1:row+1,col].astype(float)
            score=np.minimum(samples-low,high-samples).min(axis=1)
            if score[0]<0<=score[1] and score[1]>score[0]:ys[j]=row-1+(-score[0])/(score[1]-score[0])
        # Local isolated-spike rejection preserves broad peaks and curved waves.
        local=np.array([np.median(ys[max(0,i-2):i+3]) for i in range(len(ys))])
        residual=ys-local;mad=float(np.median(np.abs(residual-np.median(residual))))
        good=np.abs(residual)<=max(2.5,4.5*mad)
        if good.mean()<.8:return {**bad,'reason':'Ambiguous/occluded liquid surface'},out
        normalized=(xs-x)/w;grid=np.linspace(.15,.85,101)
        surface=np.interp(grid,normalized[good],ys[good])
        # No interpolation across missing or rejected spans >3 image columns.
        support=np.interp(grid,normalized,good.astype(float))>.99
        gx=x+w*grid
        support &= np.min(np.abs(gx[:,None]-xs[good][None,:]),axis=1)<=3.
        if support.mean()<.8:return {**bad,'reason':'Too many missing surface columns'},out
        heights=(y+h)-surface
        if self.reference_started is not None:
            self.reference_candidates.append((heights.copy(),w))
            if (time.perf_counter() if timestamp is None else timestamp)-self.reference_started>=2.:
                self.reference_started=None
                samples=np.array([a for a,b in self.reference_candidates])
                widths=np.array([b for a,b in self.reference_candidates])
                if len(samples)<30 or np.percentile(np.std(samples,axis=0),90)>1.5 or np.std(widths)>1.5:
                    self.reference_error='Reference unstable or too few frames; wait until liquid settles and capture again'
                else:
                    self.reference=np.median(samples,axis=0);self.reference_width=float(np.median(widths))
                    self.settings['reference_profile']=self.reference.tolist();self.settings['reference_width']=self.reference_width
        required=self.settings.get('reference_required',True)
        if self.reference is not None:
            if not self.reference_width or abs(w/self.reference_width-1)>.05:
                return {**bad,'reason':'Cup scale differs >5% from stationary reference; outside calibrated stop view','reference_locked':True},out
            baseline=self.reference;heights=heights*self.reference_width/w
        else:
            if len(self.baseline)<15:self.baseline.append(heights.tolist())
            baseline=np.median(self.baseline,axis=0)
        deviation=heights-baseline
        deviation[~support]=0.
        displacement=float(deviation[np.argmax(np.abs(deviation))])
        cv2.polylines(out,[hull],True,(255,180,0),2)
        cv2.rectangle(out,(x,y),(x+w,y+h),(0,220,0),1)
        for col,row,accepted in zip(xs,ys,good):
            cv2.circle(out,(int(col),int(round(row))),1,(0,220,0) if accepted else (0,0,255),-1)
        line=np.array([[int(col),int(round(row))] for col,row in zip(gx,surface)],np.int32)
        cv2.polylines(out,[line],False,(0,220,255),1)
        cv2.aruco.drawDetectedMarkers(out,[q for q,i in selected],np.array([i for q,i in selected],np.int32))
        ready=self.reference is not None if required else self.reference is not None or len(self.baseline)>=15
        reason=self.reference_error or ('Capturing stationary reference (2 seconds)' if self.reference_started is not None else 'Capture and lock stationary reference at the stop location first')
        return {**bad,'valid':ready,'reason':'' if ready else reason,
                'reference_locked':self.reference is not None,'reference_capture_active':self.reference_started is not None,
                'baseline_ready':ready,'confidence':float(good.mean()) if ready else 0.,
                'left_px':float(np.median(heights[:7])),'center_px':float(np.median(heights[47:54])),'right_px':float(np.median(heights[-7:])),
                'delta_h_px':float(np.median(heights[:7])-np.median(heights[-7:])),
                'displacement_px':displacement if ready else None,
                'surface_spatial_rms_px':float(np.sqrt(np.mean(deviation[support]**2))) if ready else None,
                'surface_spatial_peak_to_peak_px':float(np.ptp(deviation[support])) if ready else None,
                'surface_points_px':[[float(col),float(row),bool(ok)] for col,row,ok in zip(xs,ys,good)],
                'profile_displacement_px':[float(d) if ok and ready else None for d,ok in zip(deviation,support)],
                'sample_count':int(good.sum()),'valid_column_ratio':float(support.mean()),
                'blue_bbox':[x,y,w,h],'marker_ids':[i for q,i in selected],
                'rim_margin_px':None,'highest_px':float(np.max(heights[support])),
                'quality_note':'Camera/tray rotation and occlusion require visual validation; no mm calibration'},out
