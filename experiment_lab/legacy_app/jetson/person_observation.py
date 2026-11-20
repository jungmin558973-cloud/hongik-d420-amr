"""Depth measurement and conservative short-term identity. No ROS or motor I/O."""
import math
import uuid
import numpy as np


def measure_depth(depth, box, fx, cx, diagnostics=None):
    """Use agreeing neighboring torso patches; never fill holes with old/guessed depth."""
    quality = diagnostics if diagnostics is not None else {}
    x, y, w, h = box
    if (depth.ndim != 2 or not all(math.isfinite(v) for v in (x,y,w,h,fx,cx))
            or w <= 0 or h <= 0 or fx <= 0):
        raise ValueError('사람 영역·카메라 보정 정보가 올바르지 않습니다.')
    height, width = depth.shape
    columns = ((.25,.42),(.42,.58),(.58,.75))
    rows = ((.20,.36),(.36,.52),(.52,.70))
    patches = []
    total = valid_total = 0
    for row,(top,bottom) in enumerate(rows):
        for col,(left,right) in enumerate(columns):
            x1,x2 = max(0,min(width,int(x+left*w))), max(0,min(width,int(x+right*w)))
            y1,y2 = max(0,min(height,int(y+top*h))), max(0,min(height,int(y+bottom*h)))
            roi = depth[y1:y2,x1:x2]
            values = roi[np.isfinite(roi) & (roi >= .20) & (roi <= 5.)]
            total += roi.size;valid_total += values.size
            if roi.size < 32 or values.size < max(32,roi.size*.30):
                continue
            distance = float(np.median(values))
            q1,q3 = np.percentile(values,[25,75])
            if q3-q1 > max(.08,.06*distance):
                continue
            patches.append({'row':row,'col':col,'distance':distance,'values':values,
                            'bounds':[x1,y1,x2-x1,y2-y1]})
    quality.update(valid_fraction=round(valid_total/max(1,total),3),
                   valid_pixels=int(valid_total),pixels=int(total),usable_patches=len(patches))
    # Require a connected pair that includes the central torso column.
    # Side-only background or an isolated valid island must not command movement.
    groups = []
    unseen = set(range(len(patches)))
    while unseen:
        group = {unseen.pop()};pending = list(group)
        while pending:
            a = patches[pending.pop()]
            for index in list(unseen):
                b = patches[index]
                if (abs(a['row']-b['row'])+abs(a['col']-b['col']) == 1
                        and abs(a['distance']-b['distance']) <= max(.10,.07*min(a['distance'],b['distance']))):
                    unseen.remove(index);group.add(index);pending.append(index)
        if len(group)>=2 and any(patches[i]['col']==1 for i in group):
            groups.append(group)
    if not groups:
        ratio=100*quality['valid_fraction']
        raise ValueError(f'사람 영역의 유효 깊이가 부족합니다. 유효 {ratio:.0f}% · 일치 영역 없음')
    selected=min(groups,key=lambda group:np.median([patches[i]['distance'] for i in group]))
    values=np.concatenate([patches[i]['values'] for i in selected])
    distance=float(np.median(values))
    q1,q3=np.percentile(values,[25,75])
    if q3-q1>max(.12,.08*distance):
        raise ValueError('사람 영역의 깊이가 불확실합니다.')
    quality.update(selected_patches=len(selected),regions=[patches[i]['bounds'] for i in sorted(selected)])
    return distance,-math.atan2(x+w/2-cx,fx)


def iou(a, b):
    x, y = max(a[0],b[0]), max(a[1],b[1])
    right, bottom = min(a[0]+a[2],b[0]+b[2]), min(a[1]+a[3],b[1]+b[3])
    area = max(0.,right-x)*max(0.,bottom-y)
    return area / max(1., a[2]*a[3]+b[2]*b[3]-area)


class Identity:
    """One locked visual target; keep background detections separate from its identity."""
    MAX_GAP=2.0

    def __init__(self):self.reset()

    def reset(self):
        self.target_id=None;self.box=None
        self.since=self.last=0.
        self.signature=None;self.missed=False;self.stability=.4
        self.selected_indices=[];self.selected_box=None;self.ambiguous=False

    @staticmethod
    def union(a,b):
        x,y=min(a[0],b[0]),min(a[1],b[1])
        return [x,y,max(a[0]+a[2],b[0]+b[2])-x,max(a[1]+a[3],b[1]+b[3])-y]

    def split_pair(self,detections,signatures,depths):
        # At floor height, one known person's two legs can become two YOLO boxes.
        # Only associate a pair inside the preceding locked ROI with equal measured depth.
        if len(detections)!=2 or depths is None or any(d is None for d in depths):return None
        a,b=[d['box'] for d in detections]
        if abs(depths[0]-depths[1])>.12:return None
        if min(a[1],b[1])>.08*min(a[3],b[3]) or abs(a[1]-b[1])>.10*max(a[3],b[3]):return None
        if not .7<=a[3]/max(1.,b[3])<=1.4:return None
        if abs(a[1]+a[3]-b[1]-b[3])>.2*max(a[3],b[3]):return None
        horizontal_gap=max(0.,max(a[0],b[0])-min(a[0]+a[2],b[0]+b[2]))
        if horizontal_gap>.2*max(a[2],b[2]):return None
        box=self.union(a,b)
        overlap=iou(box,self.box)
        if overlap<.50 or min(iou(a,self.box),iou(b,self.box))<.12:return None
        if self.signature is None or any(sig is None or appearance_distance(sig,self.signature)>.30 for sig in signatures):return None
        signature=(signatures[0]+signatures[1])/2
        return (overlap+.25,[0,1],box,signature)

    def update(self,detections,now,signatures=None,depths=None):
        if self.box is not None and now-self.last>self.MAX_GAP:self.reset()
        self.selected_indices=[];self.selected_box=None;self.ambiguous=False
        if not detections:
            if self.box is not None:self.missed=True
            return None,False
        signatures=signatures or [None]*len(detections)
        reacquiring=self.missed or (self.box is not None and now-self.last>.4)
        new=False
        if self.box is None:
            if len(detections)!=1:self.ambiguous=True;return None,False
            members,box,signature=[0],detections[0]['box'],signatures[0];new=True
        else:
            options=[]
            for index,(d,signature) in enumerate(zip(detections,signatures)):
                overlap=iou(d['box'],self.box)
                color=appearance_distance(signature,self.signature) if signature is not None and self.signature is not None else None
                if overlap<(.25 if reacquiring else .20):continue
                if reacquiring and (color is None or color>.25):continue
                if len(detections)>1 and (color is None or color>.35):continue
                score=.7*overlap+.3*(1.-color if color is not None else overlap)
                options.append((score,[index],d['box'],signature))
            pair=self.split_pair(detections,signatures,depths) if not reacquiring else None
            if pair is not None:options.append(pair)
            options.sort(key=lambda option:option[0],reverse=True)
            if not options:
                if len(detections)==1 and not reacquiring:
                    members,box,signature=[0],detections[0]['box'],signatures[0];new=True
                else:
                    self.missed=True;self.ambiguous=len(detections)>1
                    return None,False
            elif len(options)>1 and options[0][0]-options[1][0]<.10:
                self.missed=True;self.ambiguous=True
                return None,False
            else:_,members,box,signature=options[0]
        if new:
            self.target_id=uuid.uuid4().hex;self.since=now;self.stability=.4
            self.signature=signature
        elif reacquiring:self.since,self.stability=now,.2
        if signature is not None:
            self.signature=signature if self.signature is None else .9*self.signature+.1*signature
        self.box,self.last=box,now
        self.selected_indices=list(members);self.selected_box=list(box);self.missed=False
        return self.target_id,now-self.since>=self.stability


def appearance_signature(bgr, box):
    """Ephemeral clothing-color histogram, not facial recognition or stored imagery."""
    x,y,w,h=box
    height,width=bgr.shape[:2]
    x1,x2=max(0,min(width,int(x+.25*w))),max(0,min(width,int(x+.75*w)))
    y1,y2=max(0,min(height,int(y+.2*h))),max(0,min(height,int(y+.7*h)))
    roi=bgr[y1:y2,x1:x2]
    if roi.size<96:return None
    histogram=np.concatenate([np.histogram(roi[:,:,channel],bins=16,range=(0,256))[0]
                              for channel in range(3)]).astype(np.float64)
    return histogram/histogram.sum()


def appearance_distance(a,b):
    return math.sqrt(max(0.,1.-float(np.sqrt(a*b).sum())))
