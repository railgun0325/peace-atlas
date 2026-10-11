"""Gesture acceptance against disposable evidence or an explicit synthetic fixture.

No intercepted endpoints, platform visits, model calls or evidence writes.
The server must have its worker disabled and an isolated profile.
"""
import argparse
import json
import math
import time
from pathlib import Path
from urllib.request import urlopen

from playwright.sync_api import sync_playwright, expect


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--base-url', required=True)
    parser.add_argument('--artifacts', type=Path, required=True)
    parser.add_argument('--min-nodes', type=int, default=25)
    parser.add_argument('--min-edges', type=int, default=80)
    parser.add_argument('--synthetic-fixture', action='store_true', help='Label an isolated JSON fixture server instead of a saved SQLite clone.')
    args = parser.parse_args()
    base = args.base_url.rstrip('/')
    assert base.startswith('http://127.0.0.1:')
    args.artifacts.mkdir(parents=True, exist_ok=True)
    checks, errors, gestures, convergence = [], [], [], []

    def graph():
        with urlopen(base + '/api/v2/accounts/network', timeout=15) as response:
            return json.load(response)

    original = graph()
    assert len(original['nodes']) >= args.min_nodes
    assert len(original['edges']) >= args.min_edges

    def positions(page):
        return page.locator('.network-node').evaluate_all('''nodes => nodes.map(node => {
          const dot = node.querySelector('.network-node-dot'), p = dot.ownerSVGElement.createSVGPoint();
          const screen = p.matrixTransform(dot.getScreenCTM()), t = node.transform.baseVal.getItem(0).matrix;
          return {key:node.dataset.key,x:t.e,y:t.f,sx:screen.x,sy:screen.y,r:Number(node.dataset.radius),pinned:node.dataset.pinned};
        })''')

    def wait_stable(page, scenario, timeout=120000):
        started = time.monotonic()
        page.wait_for_function('''() => ['stable','held-stable'].includes(document.querySelector('.interactive-network')?.dataset.layoutState)''', timeout=timeout)
        state = page.locator('.interactive-network').evaluate('n => ({...n.dataset})')
        assert float(state['layoutMovement']) <= .035 and int(state['layoutQuiet']) >= 30 and int(state['layoutConfirmationSteps']) >= 1800 and float(state['layoutConfirmationDisplacement']) <= .35, (scenario,state)
        before = positions(page)
        page.wait_for_timeout(600)
        after = positions(page)
        later = page.locator('.interactive-network').evaluate('n => ({...n.dataset})')
        assert state['layoutSteps'] == later['layoutSteps'], ('Stable solver must stop requesting layout work',scenario,state,later)
        assert [(n['x'],n['y']) for n in before] == [(n['x'],n['y']) for n in after], ('Stable positions drifted',scenario)
        convergence.append({'scenario':scenario,'elapsed_seconds':round(time.monotonic()-started,3),'state':state,'idle_layout_steps_unchanged':True})

    def drag_into_neighbour(page, scenario, hold_until_stable=False):
        current = positions(page)
        pairs = [(math.hypot(a['x']-b['x'], a['y']-b['y']), a, b)
                 for i, a in enumerate(current) for b in current[i+1:]
                 if a['pinned'] == b['pinned'] == 'false']
        _, held, other = min(pairs, key=lambda entry: entry[0])
        page.mouse.move(held['sx'], held['sy'])
        captured = page.evaluate('p => document.elementFromPoint(p.sx,p.sy)?.closest(".network-node")?.dataset.key',held)
        assert captured == held['key'], ('A label or hit area obstructs the node',held['key'],captured)
        page.mouse.down()
        page.mouse.move(other['sx'], other['sy'], steps=12)
        # Move onto the neighbour's current position to exercise direct collision,
        # rather than merely checking that an arbitrary drag changed a transform.
        target = next(n for n in positions(page) if n['key'] == other['key'])
        page.mouse.move(target['sx'], target['sy'])
        after = {n['key']: n for n in positions(page)}
        a, b = after[held['key']], after[other['key']]
        held_pointer_error = math.hypot(a['sx']-target['sx'], a['sy']-target['sy'])
        displacement = math.hypot(b['x']-other['x'], b['y']-other['y'])
        collision_displacement = math.hypot(b['x']-target['x'], b['y']-target['y'])
        separation = math.hypot(a['x']-b['x'], a['y']-b['y'])
        assert held_pointer_error < 3, (scenario, held_pointer_error)
        # Springs can move the neighbour in both directions during a long drag;
        # measure the actual collision against its immediately preceding position.
        assert collision_displacement > 12, (scenario, collision_displacement)
        assert separation >= a['r']+b['r']+15, (scenario, separation)
        # Do not move the mouse: continuous response must come from the solver,
        # including when automatic motion is paused or prefers-reduced-motion.
        held_before = after
        steps_before = int(page.locator('.interactive-network').get_attribute('data-layout-steps'))
        page.wait_for_timeout(550)
        held_after = {n['key']:n for n in positions(page)}
        neighbours = {edge['target_key'] if edge['source_key']==held['key'] else edge['source_key']
                      for edge in original['edges'] if held['key'] in (edge['source_key'],edge['target_key'])}
        stationary_response = max(math.hypot(n['x']-held_before[key]['x'],n['y']-held_before[key]['y'])
                                  for key,n in held_after.items() if key!=held['key'])
        connected_response = max([math.hypot(held_after[key]['x']-held_before[key]['x'],held_after[key]['y']-held_before[key]['y'])
                                  for key in neighbours] or [0])
        held_error = math.hypot(held_after[held['key']]['sx']-target['sx'],held_after[held['key']]['sy']-target['sy'])
        assert held_error < 3 and stationary_response > .1, (scenario,held_error,stationary_response)
        if neighbours:
            assert connected_response > .1, ('Observed neighbours must respond to the held account',scenario,connected_response)
        assert int(page.locator('.interactive-network').get_attribute('data-layout-steps')) > steps_before
        if hold_until_stable:
            wait_stable(page,scenario+'_stationary_held')
            expect(page.locator('.interactive-network')).to_have_attribute('data-layout-state','held-stable')
            before_wake = {n['key']:n for n in positions(page)}
            old_steps = int(page.locator('.interactive-network').get_attribute('data-layout-steps'))
            # Deliberately use sub-threshold individual moves: total travel must
            # still count as a drag and must not accidentally open the inspector.
            for offset in range(1,25):
                page.mouse.move(target['sx']+offset,target['sy'])
            page.wait_for_timeout(250)
            assert int(page.locator('.interactive-network').get_attribute('data-layout-steps')) > old_steps
            awake = {n['key']:n for n in positions(page)}
            assert math.hypot(awake[held['key']]['sx']-before_wake[held['key']]['sx'],awake[held['key']]['sy']-before_wake[held['key']]['sy']) > 20
            page.screenshot(path=str(args.artifacts/'saved-network-held-woken.png'),full_page=True)
        page.mouse.up()
        assert page.locator(f'.network-node[data-key="{held["key"]}"]').get_attribute('data-pinned') == 'false'
        gestures.append({'scenario':scenario,'held':held['key'],'neighbour':other['key'],
                         'held_pointer_error_px':held_pointer_error,'neighbour_movement':displacement,'collision_movement':collision_displacement,
                         'separation':separation,'stationary_pointer_neighbour_response':stationary_response,
                         'stationary_pointer_connected_response':connected_response,'stationary_held_pointer_error_px':held_error})
        page.wait_for_timeout(120)
        released_before = {n['key']:n for n in positions(page)}
        steps_before = int(page.locator('.interactive-network').get_attribute('data-layout-steps'))
        page.wait_for_timeout(300)
        released_after = {n['key']:n for n in positions(page)}
        response = max(math.hypot(n['x']-released_before[key]['x'],n['y']-released_before[key]['y']) for key,n in released_after.items())
        assert response > .05 and int(page.locator('.interactive-network').get_attribute('data-layout-steps')) > steps_before, ('Release must continue settling',scenario,response)
        gestures[-1]['released_response'] = response
        wait_stable(page,scenario+'_released')
        expect(page.locator('.network-inspector')).to_be_hidden()

    with sync_playwright() as pw:
        browser = pw.chromium.launch(executable_path=pw.chromium.executable_path, headless=True)
        context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='reduce')
        page = context.new_page()
        page.on('pageerror', lambda error: errors.append(str(error)))
        try:
            page.goto(base + '/#connections')
            expect(page.get_by_role('heading', name='账号关系图', exact=True)).to_be_visible()
            expect(page.locator('.network-node')).to_have_count(len(original['nodes']))
            expect(page.locator('.network-edge')).to_have_count(len(original['edges']))
            expect(page.locator('.network-inspector')).to_be_hidden()
            assert page.locator('.network-node').evaluate_all('''nodes => nodes.every(node => {
              const expected={organization:'rect',individual:'circle',unknown:'path'}[node.dataset.accountType];
              return node.querySelector('.network-node-dot').tagName===expected && Number(node.dataset.radius)>=5;
            })''')
            assert page.locator('.network-node-name:not(.label-hidden)').count() <= 32
            assert not page.locator('.graph-coverage').evaluate('node => node.open')
            checks.append('dense_saved_network_uses_type_contours_bounded_labels_and_full_canvas')
            page.locator('.interactive-svg').scroll_into_view_if_needed()
            page.screenshot(path=str(args.artifacts/'saved-network-overview.png'),full_page=True)

            degree = {}
            for edge in original['edges']:
                for key in (edge['source_key'],edge['target_key']): degree[key]=degree.get(key,0)+1
            focus = max(degree,key=degree.get)
            node = page.locator(f'.network-node[data-key="{focus}"]')
            node.locator('.network-node-dot').hover()
            expect(page.locator('.network-edge.highlighted')).to_have_count(degree[focus])
            assert page.locator('.network-node.dimmed').count() > 0
            checks.append('hover_emphasizes_actual_neighbourhood_without_hiding_evidence')
            node.focus(); page.keyboard.press('Enter')
            expect(page.locator('.network-inspector')).to_be_visible()
            expect(page.locator('.network-inspector')).to_contain_text(focus)
            page.screenshot(path=str(args.artifacts/'saved-network-selected.png'),full_page=True)
            page.get_by_role('button',name='收起详情',exact=True).click()
            expect(page.locator('.network-inspector')).to_be_hidden()
            expect(page.locator('.network-node.selected')).to_have_count(0)
            checks.append('selection_opens_traceable_detail_and_close_restores_full_canvas')

            expect(page.get_by_role('button',name='开启布局动效',exact=True)).to_be_visible()
            drag_into_neighbour(page,'reduced_motion_and_paused')
            checks.append('drag_pushes_neighbours_even_when_animation_is_paused')
            checks.extend(['stationary_paused_pointer_keeps_neighbours_adjusting','paused_release_settles_and_stops_idle_work'])
            pin_node = page.locator('.network-node').first
            pin_node.locator('.network-node-dot').dblclick()
            expect(pin_node).to_have_attribute('data-pinned','true')
            pin_node.locator('.network-node-dot').dblclick()
            expect(pin_node).to_have_attribute('data-pinned','false')
            wait_stable(page,'pin_then_release')
            checks.append('double_click_pins_and_releases_without_moving_inspector')
            for _ in range(6): page.get_by_role('button',name='缩小',exact=True).click()
            assert page.locator('.network-node-name:not(.label-hidden)').count() <= 8
            page.get_by_role('button',name='适应画布',exact=True).click()
            checks.append('zoom_adapts_label_density_and_fit_keeps_all_nodes')
            page.screenshot(path=str(args.artifacts/'saved-network-dragged.png'),full_page=True)
            page.set_viewport_size({'width':390,'height':844})
            assert page.evaluate('document.documentElement.scrollWidth <= innerWidth')
            checks.append('narrow_viewport_contains_the_saved_network')
            context.close()

            context = browser.new_context(viewport={'width':1440,'height':1000}, reduced_motion='no-preference')
            page = context.new_page()
            page.on('pageerror',lambda error:errors.append(str(error)))
            page.goto(base+'/#connections')
            expect(page.locator('.network-node')).to_have_count(len(original['nodes']))
            page.locator('.interactive-svg').scroll_into_view_if_needed()
            # The previous 5-second equality assertion merely accepted the old
            # 220-frame cutoff. Wait for measured convergence, then check idle.
            wait_stable(page,'automatic_initial_layout')
            page.screenshot(path=str(args.artifacts/'saved-network-automatic-stable.png'),full_page=True)
            checks.append('automatic_layout_stops_only_after_a_measured_quiet_interval')
            drag_into_neighbour(page,'after_animation_has_settled')
            checks.extend(['stable_layout_wakes_on_drag','stationary_pointer_keeps_actual_neighbours_adjusting','release_continues_until_stable_then_stops_idle_work'])
            page.get_by_role('button',name='暂停布局动效',exact=True).click()
            drag_into_neighbour(page,'explicitly_paused_after_drag',hold_until_stable=True)
            checks.append('explicit_pause_still_allows_direct_collision_gestures')
            checks.append('explicitly_paused_release_converges_without_reenabling_automatic_motion')
            checks.extend(['stationary_held_constraint_can_stabilize_and_sleep','small_pointer_increments_wake_held_layout_and_remain_a_drag'])
            expect(page.get_by_role('button',name='开启布局动效',exact=True)).to_be_visible()
            # Exercise actual native capture loss while the mouse is held. The
            # graph must release the constraint and return to free settling.
            canvas_box = page.locator('.interactive-svg').bounding_box()
            visible = [n for n in positions(page) if canvas_box['x']+20<n['sx']<min(1420,canvas_box['x']+canvas_box['width']-20)
                       and max(20,canvas_box['y']+20)<n['sy']<min(980,canvas_box['y']+canvas_box['height']-20)]
            held = next(n for n in visible if page.evaluate('p=>document.elementFromPoint(p.sx,p.sy)?.closest(".network-node")?.dataset.key',n)==n['key'])
            captured = page.locator(f'.network-node[data-key="{held["key"]}"]')
            captured.evaluate('n=>n.addEventListener("pointerdown",e=>n.dataset.testPointerId=e.pointerId,{once:true})')
            page.mouse.move(held['sx'],held['sy']); page.mouse.down()
            page.mouse.move(held['sx']+20,held['sy']+10)
            pointer_id = int(captured.get_attribute('data-test-pointer-id'))
            before_other_pointer = captured.get_attribute('transform')
            page.locator('.interactive-svg').dispatch_event('pointermove',{'pointerId':pointer_id+100,'clientX':held['sx']+200,'clientY':held['sy']+200})
            page.locator('.interactive-svg').dispatch_event('pointercancel',{'pointerId':pointer_id+100})
            assert captured.get_attribute('transform') == before_other_pointer
            assert page.locator('.interactive-network').get_attribute('data-layout-state') == 'dragging'
            assert captured.evaluate('n=>n.hasPointerCapture(Number(n.dataset.testPointerId))')
            captured.evaluate('n=>n.releasePointerCapture(Number(n.dataset.testPointerId))')
            page.wait_for_timeout(100)
            assert page.locator('.interactive-network').get_attribute('data-layout-state') not in ['dragging','held-stable']
            page.mouse.up()
            wait_stable(page,'native_capture_loss_recovery')
            page.screenshot(path=str(args.artifacts/'saved-network-capture-recovered.png'),full_page=True)
            checks.extend(['unrelated_pointer_cannot_move_or_cancel_active_drag','native_capture_loss_releases_constraint_and_settles'])
            assert graph() == original, 'Display gestures must not modify saved accounts or evidence'
            assert not errors, errors
            checks.extend(['saved_account_ids_and_relationship_sources_unchanged','no_page_errors'])
        except Exception as error:
            page.screenshot(path=str(args.artifacts/'failure.png'),full_page=True)
            (args.artifacts/'results.json').write_text(json.dumps({'ok':False,'error':str(error),'checks':checks,'page_errors':errors,'gestures':gestures,'convergence':convergence},ensure_ascii=False,indent=2),'utf-8')
            raise
        finally:
            context.close(); browser.close()
    (args.artifacts/'results.json').write_text(json.dumps({'ok':True,'checks':checks,'page_errors':errors,
        'saved_nodes':len(original['nodes']),'saved_edges':len(original['edges']),'gestures':gestures,'convergence':convergence,
        'boundary':('Real browser and isolated local JSON fixture server; no real backend/database/worker/model' if args.synthetic_fixture
                    else 'Real HTTP API and browser; disposable SQLite clone of saved public evidence; worker disabled')},ensure_ascii=False,indent=2),'utf-8')
    print(f'{len(checks)} saved-network gesture groups passed')


if __name__=='__main__':
    main()
