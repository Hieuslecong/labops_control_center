from pathlib import Path
import os
from playwright.sync_api import sync_playwright
p=Path(__file__).resolve().parent.parent; out=p/'visuals'; out.mkdir(exist_ok=True)
errors=[]; checks=[]
with sync_playwright() as pw:
 b=pw.chromium.launch(executable_path=os.environ.get('CHROMIUM_PATH', '/usr/bin/chromium'),args=['--no-sandbox'])
 page=b.new_page(viewport={'width':1600,'height':1100},device_scale_factor=1,accept_downloads=True)
 page.on('pageerror',lambda e:errors.append(str(e)))
 page.set_content((p/'04_Interactive_Prototype.html').read_text(encoding='utf8')); page.wait_for_selector('#kpis .kpi'); assert page.locator('#kpis .kpi').count()==6; assert page.locator('tbody tr').count()==5; assert page.locator('text=Chưa có dữ liệu lịch sử đã xác minh').count()==1; checks.append('default six KPIs, five snapshot rows, history empty')
 page.screenshot(path=str(out/'01_overview_no_history.png'),full_page=True)
 page.check('#demo'); assert page.locator('svg.chart').count()==1; page.screenshot(path=str(out/'01_overview.png'),full_page=True); checks.append('explicit demo switch renders labeled history')
 page.click('[data-tab=live]'); page.screenshot(path=str(out/'02_live.png'),full_page=True); page.select_option('#member','nhannv'); assert page.locator('tbody tr').count()==2; page.fill('#search','3059478'); assert page.locator('tbody tr').count()==1; checks.append('member and PID filters')
 with page.expect_download() as dl: page.click('#export')
 d=dl.value; assert d.suggested_filename=='prototype_snapshot.csv'; assert '3059478' in Path(d.path()).read_text(); checks.append('filtered CSV export')
 page.click('#clear'); page.click('[data-sort=elapsed_seconds]'); assert '555432' in page.locator('tbody tr').first.inner_text(); page.click('[data-sort=elapsed_seconds]'); assert '3304598' in page.locator('tbody tr').first.inner_text(); checks.append('runtime numeric sorting both directions'); page.click('[data-sort=cpu]'); assert '3059478' in page.locator('tbody tr').first.inner_text(); checks.append('CPU sorting')
 page.click('#ramToggle'); assert 'RSS cộng dồn' not in page.locator('thead').inner_text(); page.click('#ramToggle'); checks.append('column toggle')
 page.click('#rawMode'); assert page.get_by_text('Chưa có dữ liệu raw').count()==1; page.click('#workloadMode'); checks.append('raw diagnostics empty state')
 page.click('[data-detail="3304598"]'); assert page.locator('#drawer').is_visible(); assert page.locator('#drawer button:disabled').count()==2; page.screenshot(path=str(out/'05_detail.png'),full_page=False); page.keyboard.press('Escape'); assert not page.locator('#drawer').is_visible(); checks.append('detail drawer / disabled destructive actions / Escape')
 page.click('[data-tab=analytics]'); page.screenshot(path=str(out/'03_analytics.png'),full_page=True); page.click('[data-tab=history]'); page.screenshot(path=str(out/'04_history.png'),full_page=True)
 page.click('[data-tab=overview]'); page.select_option('#node','rack'); assert '—' in page.locator('.kpi .value').first.inner_text(); assert '0/3' in page.locator('.kpi .value').nth(4).inner_text(); checks.append('offline scope does not fabricate zero jobs')
 page.click('#clear'); page.select_option('#node','ws'); assert page.locator('.attention').get_by_text('Rack offline').count()==0; checks.append('attention matches selected node scope')
 page.select_option('#member','nhannv'); bar=page.locator('.barrow').filter(has_text='@nhannv').first.locator('.bar').evaluate('(e)=>getComputedStyle(e).backgroundColor'); page.select_option('#member','all'); bar2=page.locator('.barrow').filter(has_text='@nhannv').first.locator('.bar').evaluate('(e)=>getComputedStyle(e).backgroundColor'); assert bar==bar2; checks.append('stable owner color across filters')
 before=page.locator('svg.chart polyline').first.get_attribute('points'); live=page.locator('.kpi .value').first.inner_text(); page.select_option('#range','7d'); assert page.locator('svg.chart polyline').first.get_attribute('points')!=before; assert page.locator('.kpi .value').first.inner_text()==live; checks.append('range changes demo chart not live KPIs')
 page.select_option('#gpu','gpu'); page.select_option('#node','rack'); assert page.locator('#gpu').input_value()=='all' and page.locator('#gpu').is_disabled(); assert page.locator('#member').is_disabled(); checks.append('incompatible rack filters cleared')
 page.click('#clear'); assert not page.locator('#gpu').is_disabled(); page.click('#theme'); page.screenshot(path=str(out/'06_dark.png'),full_page=True); checks.append('theme toggle')
 page.set_viewport_size({'width':390,'height':844}); page.screenshot(path=str(out/'07_mobile.png'),full_page=True); assert page.evaluate('document.documentElement.scrollWidth <= window.innerWidth+1'); checks.append('390px mobile viewport without page-level horizontal overflow')
 b.close()
assert not errors,errors
(p/'11_Prototype_QA.md').write_text('# Prototype verification\n\nLocal Chromium via Playwright, 25/11/2026. Standalone HTML only; no LabOps API or production connection.\n\n'+'\n'.join('- PASS — '+c for c in checks)+'\n- PASS — no pageerror events in tested paths.\n\nNot verified: application code, staging/production API, authentication, real telemetry, process actions, migration, network performance, full WCAG audit. Screenshots are captured from the local prototype.\n',encoding='utf8')
print('PASS',len(checks), 'checks; screenshots:',len(list(out.glob('*.png'))))
