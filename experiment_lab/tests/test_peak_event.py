from lab.peak_event import export_peak
from lab.storage import dump


def test_missing_surface_never_becomes_zero_peak(tmp_path):
    result=export_peak(tmp_path,[{'utc':1,'valid':False}],[],[],{})
    assert not result['available'] and 'peak_rise_px' not in result


def test_peak_is_maximum_rise_in_stop_window_not_largest_downward_trough(tmp_path):
    import cv2,numpy as np,csv
    writer=cv2.VideoWriter(str(tmp_path/'webcam_raw.mp4'),cv2.VideoWriter_fourcc(*'mp4v'),30,(64,48))
    for _ in range(3):writer.write(np.full((48,64,3),100,np.uint8))
    writer.release()
    frames=[{'utc':0.,'seq':0,'valid':True,'profile_displacement_px':[100.]},
            {'utc':1.,'seq':1,'valid':True,'profile_displacement_px':[0.,6.,1.],'surface_points_px':[[20.,20.,True]]},
            {'utc':2.,'seq':2,'valid':True,'profile_displacement_px':[-20.,0.]}]
    events=[{'event':'BRAKE_REQUEST','segment':'forward','utc':1.}, {'event':'STOP_CONFIRMED','segment':'forward','utc':2.}]
    with (tmp_path/'imu.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=['utc','roll_deg']);w.writeheader();w.writerow({'utc':1.1,'roll_deg':2})
    result=export_peak(tmp_path,frames,[{'utc':1.,'v':.12}],events,{'quality':'good','jetson_minus_windows_s':.1,'uncertainty_s':.01})
    assert result['peak_rise_px']==6 and result['frame_seq']==1
    assert result['stop_window_confirmed']
    assert (tmp_path/'peak_frame.png').exists()
    with (tmp_path/'peak_imu_window.csv').open(encoding='utf-8-sig') as f:row=next(csv.DictReader(f))
    assert abs(float(row['relative_peak_s']))<1e-9
