"""Real Chromium UI integration test against a running MOCK server only."""
from pathlib import Path
import json
import time
from playwright.sync_api import sync_playwright
import requests
import os

BASE=os.environ.get('LAB_TEST_URL','http://127.0.0.1:18865')


def main():
    assert requests.get(BASE+'/api/status').json()['mode']=='MOCK','Never run UI automation against real mode'
    out=Path(__file__).resolve().parents[1]/'validation';out.mkdir(exist_ok=True)
    errors=[]
    with sync_playwright() as p:
        browser=p.chromium.launch(channel='chrome',headless=True)
        page=browser.new_page(viewport={'width':1540,'height':1100},device_scale_factor=1)
        page.on('pageerror',lambda e:errors.append(str(e)))
        page.goto(BASE+'/experiment-lab');page.wait_for_function("() => document.querySelector('#mode').textContent==='MOCK'")
        with page.expect_response('**/api/camera') as response:page.locator('#camera').click()
        assert response.value.status==200
        deadline=time.monotonic()+10
        while time.monotonic()<deadline:
            page.wait_for_timeout(250)
            if requests.get(BASE+'/api/status').json()['camera'].get('ready'):break
        page.wait_for_function("() => lastState.camera.ready && canvas.width===640")
        page.wait_for_timeout(600)
        # Two independent boxes, using actual display-to-image coordinate conversion.
        for button,rect in [('liquid',(214,140,202,140)),('anchor',(225,75,50,28))]:
            page.locator('#'+button).click();page.locator('#roiCanvas').scroll_into_view_if_needed();box=page.locator('#roiCanvas').bounding_box()
            sx=box['width']/640;sy=box['height']/360;x,y,w,h=rect
            page.mouse.move(box['x']+x*sx,box['y']+y*sy);page.mouse.down()
            page.mouse.move(box['x']+(x+w)*sx,box['y']+(y+h)*sy,steps=4);page.mouse.up()
        with page.expect_response('**/api/roi') as response:page.locator('#roiSave').click()
        assert response.value.status==200,response.value.text()
        page.locator('#queueMake').click();page.wait_for_function("() => document.querySelectorAll('.queueItem').length===108")
        page.locator('#timeline').fill('0');page.locator('#timeline').dispatch_event('change')
        for key in ('space_clear','physical_stop_ready','cup_secure','origin_checked','operator_present'):page.locator('#'+key).check()
        page.locator('#arm').click();page.wait_for_function("() => document.querySelector('#state').textContent.startsWith('ARMED')")
        with page.expect_response('**/api/start') as response:page.locator('#start').click()
        assert response.value.status==200,response.value.text()
        page.wait_for_function("() => document.querySelector('#state').textContent.startsWith('RECORDING')",timeout=10000)
        page.screenshot(path=str(out/'dashboard_recording.png'),full_page=True)
        states=[];deadline=time.monotonic()+80
        while time.monotonic()<deadline:
            # Keep browser timers alive while mock robot runs; no control network outside loopback.
            page.wait_for_timeout(500);s=requests.get(BASE+'/api/status').json();states.append(s['state'])
            if s['state'] in ('COMPLETE','ABORTED','ERROR') and not s['saving']:break
        assert s['state']=='COMPLETE',s.get('error') or s
        rid=s['run_id'];page.locator('#refresh').click();page.wait_for_timeout(1000)
        page.locator('#runs button').first.click();page.wait_for_timeout(600)
        page.locator('#peak').click();page.wait_for_timeout(300)
        page.screenshot(path=str(out/'dashboard_results.png'),full_page=True)
        for f in ('webcam_raw.mp4','webcam_annotated.mp4','camera_frames.csv','synchronized.csv','metrics.json','report.html','plots/slosh.png','manifest.json'):
            r=requests.get(f'{BASE}/files/{rid}/{f}');assert r.status_code==200,(f,r.status_code)
        assert not errors,errors
        browser.close()
    result={'run_id':rid,'states_seen':list(dict.fromkeys(states)),'javascript_errors':errors,'result':'PASS','mode':'MOCK only'}
    (out/'ui-test-results.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result))


if __name__=='__main__':main()
