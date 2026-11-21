"""Separate cup anchor and liquid ROI; low confidence yields null measurements."""
import cv2
import numpy as np
import math


class Tracker:
    def __init__(self,settings=None):
        self.settings=settings or {};self.template=None;self.base=None;self.anchor=None;self.last=None
        self.baselines=[]

    def reset(self,settings):self.__init__(settings)

    @staticmethod
    def rect(v,shape):
        if not isinstance(v,(list,tuple)) or len(v)!=4 or any(type(x) not in (int,float) or not math.isfinite(x) for x in v):raise ValueError('Drag ROI first')
        x,y,w,h=map(int,v)
        if min(x,y)<0 or w<8 or h<8 or x+w>shape[1] or y+h>shape[0]:raise ValueError('ROI outside frame')
        return x,y,w,h

    def process(self,frame,elapsed=0.):
        if self.settings.get('method')=='marker_blue':
            from .marker_blue import MarkerBlue
            if not hasattr(self,'marker_blue'):self.marker_blue=MarkerBlue(self.settings)
            return self.marker_blue.process(frame)
        overlay=frame.copy();invalid={'valid':False,'reason':'ROI/anchor not set','confidence':0.,
                                      'left_px':None,'center_px':None,'right_px':None,
                                      'displacement_px':None,'rim_margin_px':None,'delta_h_px':None}
        if elapsed<self.settings.get('appearance_s',0.):return {**invalid,'reason':'not visible (before cup appearance)'},overlay
        try:
            liquid=self.rect(self.settings.get('liquid_roi'),frame.shape)
            anchor=self.rect(self.settings.get('anchor_roi'),frame.shape)
        except ValueError as e:return {**invalid,'reason':str(e)},overlay
        gray=cv2.cvtColor(frame,cv2.COLOR_BGR2GRAY)
        if self.template is None:
            x,y,w,h=anchor;self.template=gray[y:y+h,x:x+w].copy()
            if self.template.std()<8:return {**invalid,'reason':'Anchor lacks texture; select cup rim/marker'},overlay
            self.anchor=anchor;self.last=(x,y)
        x,y,w,h=self.anchor;lx,ly=self.last;radius=int(self.settings.get('search_radius',80))
        x0=max(0,lx-radius);y0=max(0,ly-radius)
        x1=min(gray.shape[1],lx+w+radius);y1=min(gray.shape[0],ly+h+radius)
        search=gray[y0:y1,x0:x1]
        if search.shape[0]<h or search.shape[1]<w:return {**invalid,'reason':'not visible'},overlay
        scores=cv2.matchTemplate(search,self.template,cv2.TM_CCOEFF_NORMED)
        _,score,_,pos=cv2.minMaxLoc(scores);nx,ny=x0+pos[0],y0+pos[1]
        # Do not hunt the entire background after failure. User reanchors explicitly.
        if score<float(self.settings.get('anchor_confidence',.80)) or math.hypot(nx-lx,ny-ly)>radius*.8:
            return {**invalid,'reason':'Anchor lost/occluded/scale changed; reanchor required','confidence':float(score)},overlay
        self.last=(nx,ny);dx,dy=nx-x,ny-y
        rx,ry,rw,rh=liquid;rx+=dx;ry+=dy
        if rx<0 or ry<0 or rx+rw>gray.shape[1] or ry+rh>gray.shape[0]:return {**invalid,'reason':'not visible (outside view)'},overlay
        crop=frame[ry:ry+rh,rx:rx+rw]
        hsv=cv2.cvtColor(crop,cv2.COLOR_BGR2HSV)
        lower=np.array(self.settings.get('hsv_lower',[70,60,40]),dtype=np.uint8)
        upper=np.array(self.settings.get('hsv_upper',[110,255,255]),dtype=np.uint8)
        mask=cv2.inRange(hsv,lower,upper)
        if self.settings.get('method')=='edge':
            # Meniscus contrast mode for uncoloured water. Manual visual validation required.
            g=cv2.GaussianBlur(cv2.cvtColor(crop,cv2.COLOR_BGR2GRAY),(5,5),0)
            edge=np.abs(cv2.Sobel(g,cv2.CV_32F,0,1,ksize=3))
            edge[:3]=0;edge[-3:]=0
            mask=np.zeros_like(mask)
            threshold=float(self.settings.get('edge_threshold',50.))
            for col in range(edge.shape[1]):
                row=int(np.argmax(edge[:,col]))
                if edge[row,col]>=threshold:mask[max(0,row-1):row+2,col]=255
        inset=max(2,int(rw*.08));mask[:,:inset]=0;mask[:,-inset:]=0
        heights=[];xs=[]
        for col in range(inset,rw-inset):
            rows=np.flatnonzero(mask[:,col])
            if len(rows)>=2:heights.append(float(rows[0]));xs.append(col)
        coverage=len(xs)/max(1,rw-2*inset)
        if coverage<.65:return {**invalid,'reason':'Liquid support too low; adjust colour threshold/lighting','confidence':float(score*coverage)},overlay
        xx=np.array(xs);yy=np.array(heights)
        # Reject multiple incompatible lines/grid clutter rather than fabricate a surface.
        coeff=np.polyfit(xx,yy,1);residual=np.abs(yy-np.polyval(coeff,xx))
        inliers=residual<max(3.,rh*.06)
        if inliers.mean()<.8:return {**invalid,'reason':'Ambiguous liquid line/grid/refraction','confidence':float(score*inliers.mean())},overlay
        yy=yy[inliers];xx=xx[inliers];coeff=np.polyfit(xx,yy,1)
        positions=np.polyval(coeff,[rw*.2,rw*.5,rw*.8])
        if len(self.baselines)<15:self.baselines.append(positions.tolist());self.base=np.median(self.baselines,axis=0)
        deviation=self.base-positions
        signed=float(deviation[int(np.argmax(np.abs(deviation)))])
        result={'valid':True,'reason':'','confidence':float(score*coverage*inliers.mean()),
                'left_px':float(-positions[0]),'center_px':float(-positions[1]),'right_px':float(-positions[2]),
                'highest_px':float(-np.min(yy)),'delta_h_px':float(positions[2]-positions[0]),
                'displacement_px':signed,'rim_margin_px':float(np.min(yy)),
                'asymmetry_px':float(deviation[0]+deviation[2]-2*deviation[1]),
                'baseline_ready':len(self.baselines)>=15,'anchor_x':nx,'anchor_y':ny,'coverage':coverage}
        scale=self.settings.get('mm_per_px')
        if self.settings.get('calibration_verified') is True and type(scale) in (int,float) and 0<scale<10:
            result['displacement_mm']=signed*scale
        cv2.rectangle(overlay,(nx,ny),(nx+w,ny+h),(0,180,255),2)
        cv2.rectangle(overlay,(rx,ry),(rx+rw,ry+rh),(60,220,60),2)
        points=np.array([[rx+int(k),ry+int(np.polyval(coeff,k))] for k in range(inset,rw-inset)],np.int32)
        cv2.polylines(overlay,[points],False,(0,0,255),2)
        cv2.putText(overlay,f'valid {result["confidence"]:.2f}  dh={signed:.1f}px',(12,26),cv2.FONT_HERSHEY_SIMPLEX,.6,(0,220,0),2)
        return result,overlay


def synthetic_frame(t,width=640,height=360):
    """Clearly synthetic visual fixture, not an instrument reading."""
    frame=np.full((height,width,3),(221,228,218),np.uint8)
    x=int(210+8*math.sin(t*.15));top=65;w=210;bottom=310
    cv2.rectangle(frame,(x,top),(x+w,bottom),(80,80,80),3)
    rng=np.random.default_rng(420);patch=rng.integers(0,255,(28,50),dtype=np.uint8)
    frame[75:103,x+15:x+65]=cv2.cvtColor(patch,cv2.COLOR_GRAY2BGR)
    amplitude=14*math.exp(-max(0.,t-5.)/1.8) if t>=5 else .3
    level=195;tilt=amplitude*math.sin(2*math.pi*1.5*t)
    poly=np.array([[x+4,int(level-tilt)],[x+w-4,int(level+tilt)],[x+w-4,bottom-4],[x+4,bottom-4]],np.int32)
    cv2.fillPoly(frame,[poly],(220,180,20))
    cv2.putText(frame,'SYNTHETIC / NOT REAL HARDWARE',(10,height-12),cv2.FONT_HERSHEY_SIMPLEX,.55,(30,30,30),1)
    return frame


SYNTHETIC_ROI={'liquid_roi':[214,140,202,140],'anchor_roi':[225,75,50,28],
               'appearance_s':0.,'hsv_lower':[70,60,40],'hsv_upper':[110,255,255]}
