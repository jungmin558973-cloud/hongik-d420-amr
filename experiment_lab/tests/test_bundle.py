from dataclasses import asdict
import zipfile
from lab.core import Config
from lab.storage import Store


def test_automatic_bundle_has_video_csv_plots_manifest_and_no_recursive_zip(tmp_path):
    s=Store(tmp_path);rid,path=s.begin(asdict(Config(s_curve=False)))
    (path/'webcam_raw.avi').write_bytes(b'preserved video example')
    (path/'robot_telemetry.jsonl').write_text('')
    result=s.finalize(rid,'COMPLETE',{'quality':'good','jetson_minus_windows_s':0,'uncertainty_s':.001})
    assert result['bundle']['ready']
    bundle=s.bundle_path(rid);assert bundle.name.endswith('_실패.zip')
    folder=s.path(rid).name
    with zipfile.ZipFile(bundle) as z:
        assert z.testzip() is None
        names=z.namelist()
        for suffix in ['webcam_raw.avi','synchronized.csv','imu.csv','plots/velocity.png','report.html','manifest.json']:
            assert folder+'/'+suffix in names
        assert not any(n.endswith('.zip') for n in names)
        assert z.read(folder+'/webcam_raw.avi')==b'preserved video example'
