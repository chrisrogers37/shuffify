"""The dedicated task's real template and script over the offline page runtime."""

import json
from pathlib import Path

from tests.page_runtime import render_page, run_page
from tests.test_playlist_operations_runtime import node as node  # noqa: F401

ROOT = Path(__file__).resolve().parent.parent
BASE = "/playlist/target/routines/new"
REV = "a" * 64


def context(rows=None):
    return {
        "success": True, "target": {"id": "target", "name": "Mix <script>"},
        "prerequisites": {
            "sources": [{"id": 4, "name": "rainy day", "playlist_id": None, "search_query": "rainy day"}],
            "routine_only_sources": [],
            "collection": {"playlist_id": "collection", "name": "Inbox", "drip_enabled": True, "drip_count": 3},
            "archive": {"playlist_id": "archive", "name": "Archive"},
        },
        "dependency_revision": REV, "routines": rows or [],
        "algorithms": [{"class_name": "BasicShuffle", "name": "Basic Shuffle", "description": "Shuffle tracks",
                        "parameters": {"keep_first": {"type": "integer", "min": 0, "max": 20, "default": 0,
                                                       "description": "Keep this many first"}}}],
        "capabilities": {name: {"available": True, "reason": None} for name in
                         ("raid", "raid_and_shuffle", "raid_and_drip", "shuffle", "rotate", "drip")},
    }


def exact_row():
    return {
        "schedule_id": 7, "schedule_revision": REV, "state": "Paused",
        "schedule": {"id": 7, "is_enabled": False, "target_playlist_name": "Mix <script>"},
        "definition": {"job_type": "shuffle", "algorithm_name": "BasicShuffle",
                       "algorithm_params": {"keep_first": 4}, "source_playlist_ids": [],
                       "schedule_type": "interval", "schedule_value": "daily", "timezone": "UTC"},
        "timing": {"kind": "interval", "interval": "daily", "timezone": "UTC", "text": "Every day",
                   "legacy": False},
        "registration": {"status": "paused", "next_run_at": None},
    }


def page_html(db_app, *, schedule_id=None):
    urls = {
        "context": "/playlist/target/routines/context", "review": "/playlist/target/routines/review",
        "activate": "/playlist/target/routines/activate", "state": "/routines",
        "setup": "/playlist/target/setup", "self": BASE, "workshop": "/workshop/target",
        "management": "/schedules",
    }
    return render_page(db_app, "routine_setup.html", path=BASE, routine_page={
        "ownerId": 1, "targetId": "target", "requestedScheduleId": schedule_id,
        "origin": "routines" if schedule_id else "playlist", "urls": urls,
    })


def test_all_six_operations_and_unknown_replay_use_the_same_request(node, db_app):
    html = page_html(db_app)
    scenario = """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:(url) => page.reply(CONTEXT)});
await settle();
assert.equal(page.byId('routine-goal-step').hidden, false);
assert.equal(page.calls.filter(call => call.method !== 'GET').length, 0);
page.$('[data-goal="all"]').click();
const operations = page.byId('routine-operation');
assert.deepEqual(operations.children.map(node => node.value),
    ['raid','raid_and_shuffle','raid_and_drip','shuffle','rotate','drip']);
for (const kind of ['raid','shuffle','raid_and_shuffle','raid_and_drip','rotate','drip']) {
    operations.value = kind; page.change(operations);
    assert.equal(page.byId('routine-operation').value, kind);
}
operations.value = 'raid'; page.change(operations);
page.byId('routine-next').click();
assert.equal(page.byId('routine-timing-step').hidden, false);
page.replies.push(page.reply({success:true, definition:{job_type:'raid',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:null,algorithm_params:{},
    schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},
    resource_scope:{target_id:'target',source_ids:[4],routine_only_source_playlist_ids:[],
    collection_id:1,archive_id:2},dependency_revision:REV,schedule_revision:null,
    candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.byId('routine-review').click(); await settle();
assert.equal(page.byId('routine-review-step').hidden, false);
assert.equal(page.calls.filter(call => call.url.endsWith('/review')).length, 1);
page.replies.push(() => { throw new Error('lost activation reply'); });
page.byId('routine-activate').click(); await settle();
assert.equal(page.byId('routine-check').hidden, false);
const first = page.calls.filter(call => call.url.endsWith('/activate'))[0];
assert.ok(first);
page.replies.push(page.reply({success:true,accepted:true,schedule_id:9}));
page.byId('routine-check').click(); await settle();
assert.equal(page.byId('routine-check').hidden, false);
assert.equal(page.byId('routine-result-step').hidden, true);
page.replies.push(page.reply({success:true,accepted:true,replayed:true,accepted_at:'2026-10-13T00:00:00Z',
    schedule_id:9,schedule_revision:REV,dependency_revision:REV,state:'Active',
    schedule:{id:9,is_enabled:true,target_playlist_id:'target'},
    registration:{status:'registered',next_run_at:'2026-10-14T00:00:00Z'}}));
page.byId('routine-check').click(); await settle();
const accepted = page.calls.filter(call => call.url.endsWith('/activate'));
assert.equal(accepted.length, 3);
assert.equal(accepted[0].body, accepted[1].body);
assert.equal(accepted[1].body, accepted[2].body);
assert.equal(page.byId('routine-result-step').hidden, false);
assert.match(page.text(page.byId('routine-result-details')), /Next run 2026-10-14/);
assert.equal(page.$('#routine-result-actions a[href="/schedules?routine_id=9&view=history"]') !== null, true);
"""
    script = "const CONTEXT = " + json.dumps(context()) + "; const REV = " + json.dumps(REV) + ";\n" + scenario
    run_page(node, html, script)


def test_exact_edit_is_compact_and_switch_clears_algorithm_without_unpausing(node, db_app):
    html = page_html(db_app, schedule_id=7)
    script = "const CONTEXT = " + json.dumps(context([exact_row()])) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new?schedule_id=7&from=routines',
    route:() => page.reply(CONTEXT)});
await settle();
assert.equal(page.byId('routine-goal-step').hidden, true);
assert.equal(page.byId('routine-connections-step').hidden, false);
assert.equal(page.byId('routine-timing-step').hidden, false);
assert.equal(page.byId('routine-progress').hidden, true);
assert.equal(page.byId('routine-next').hidden, true);
assert.equal(page.byId('routine-operation').value, 'shuffle');
assert.match(page.text(page.byId('routine-summary-list')), /#7/);
page.byId('routine-operation').value = 'raid'; page.change(page.byId('routine-operation'));
page.replies.push(page.reply({success:true, definition:{job_type:'raid',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:null,algorithm_params:{},
    schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},resource_scope:{target_id:'target',
    source_ids:[4],routine_only_source_playlist_ids:[],collection_id:1,archive_id:2},
    dependency_revision:REV,schedule_revision:REV,candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.byId('routine-review').click(); await settle();
const call = page.calls.find(item => item.url.endsWith('/review'));
const body = JSON.parse(call.body);
assert.equal(body.schedule_id, 7);
assert.equal(body.expected_revision, REV);
assert.equal(body.definition.job_type, 'raid');
assert.equal(body.definition.algorithm_name, null);
assert.deepEqual(body.definition.algorithm_params, {});
assert.equal('timing' in body.definition, false);
assert.equal('desired_enabled' in body, false);
page.replies.push(page.reply({success:true,accepted:true,replayed:false,
    accepted_at:'2026-10-13T00:00:00Z',dependency_revision:REV,schedule_id:8,
    schedule_revision:REV,state:'Active',schedule:{id:8,target_playlist_id:'target',is_enabled:true},
    registration:{status:'registered',next_run_at:'2026-10-14T00:00:00Z'}}));
page.byId('routine-activate').click(); await settle();
assert.equal(page.byId('routine-check').hidden,false);
assert.equal(page.byId('routine-result-step').hidden,true);
"""
    run_page(node, html, script)


def test_setup_continuation_is_minimal_and_stale_exact_revision_requires_adoption(node, db_app):
    html = page_html(db_app)
    changed = exact_row()
    changed["schedule_revision"] = "b" * 64
    script = "const CONTEXT = " + json.dumps(context([exact_row()])) + "; const CHANGED = " + json.dumps(
        context([changed])) + "; const REV = " + json.dumps(REV) + ";\n" + """
const saved = new Map();
const storage = {getItem:key => saved.get(key) || null,
    setItem:(key,value) => saved.set(key,value), removeItem:key => saved.delete(key)};
const first = loadPage(TREE, {path:'/playlist/target/routines/new', before:window => {window.sessionStorage=storage;},
    route:() => page.reply(CONTEXT)});
await settle();
first.$('[data-goal="all"]').click();
first.byId('routine-operation').value='shuffle'; first.change(first.byId('routine-operation'));
first.$('[data-action-click="routine.choose"]').click();
first.$('[data-action-click="routine.setup"][data-section="sources"]').click(); await settle();
assert.equal(first.navigations.length, 1);
assert.match(first.navigations[0], /section=sources/);
const raw = saved.get('shuffify:routine-continuation:v1');
assert.ok(raw);
const stored = JSON.parse(raw);
assert.equal(stored.scheduleId, 7);
assert.equal(stored.scheduleRevision, REV);
stored.origin = 'routines';
saved.set('shuffify:routine-continuation:v1', JSON.stringify(stored));
for (const forbidden of ['intent','request_key','payload','source_playlist_ids'])
    assert.equal(raw.includes(forbidden), false);
const second = loadPage(TREE, {path:'/playlist/target/routines/new',
    before:window => {window.sessionStorage=storage;}, route:() => page.reply(CHANGED)});
await settle();
assert.equal(second.byId('routine-error-step').hidden, false);
assert.equal(second.byId('routine-close').href, '/schedules');
assert.equal(second.byId('routine-error-back').href, '/schedules');
assert.equal(second.byId('routine-adopt-current').hidden, false);
assert.equal(saved.has('shuffify:routine-continuation:v1'), false);
second.byId('routine-adopt-current').click();
assert.equal(second.byId('routine-connections-step').hidden, false);
assert.equal(second.byId('routine-operation').value, 'shuffle');
saved.set('shuffify:routine-continuation:v1', JSON.stringify(stored));
const failed = loadPage(TREE, {path:'/playlist/target/routines/new',
    before:window => {window.sessionStorage=storage;},
    route:() => page.reply({success:false,error_code:'prerequisites_unavailable',message:'Try again.'},503)});
await settle();
assert.equal(failed.byId('routine-error-step').hidden,false);
assert.equal(failed.byId('routine-close').href,'/schedules');
assert.equal(failed.byId('routine-error-back').href,'/schedules');
assert.equal(saved.has('shuffify:routine-continuation:v1'),true);
"""
    run_page(node, html, script)


def test_same_type_edit_keeps_existing_option_and_untouched_legacy_timing(node, db_app):
    row = exact_row()
    row["timing"] = {"kind": "advanced", "expression": "0 9 */3 * *", "timezone": None,
                     "effective_timezone": "UTC", "legacy": True,
                     "text": "Calendar day */3 at 09:00 UTC", "explanation": "Calendar day */3 is not fixed 72 hours."}
    row["definition"]["timezone"] = None
    html = page_html(db_app, schedule_id=7)
    script = "const CONTEXT = " + json.dumps(context([row])) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new?schedule_id=7',
    route:() => page.reply(CONTEXT)});
await settle();
assert.match(page.text(page.byId('routine-legacy-timing')), /Legacy timing|legacy scheduler timezone/i);
page.replies.push(page.reply({success:true,definition:{},resource_scope:{},dependency_revision:'a'.repeat(64),
    schedule_revision:'a'.repeat(64),candidate_schedule_ids:[],warnings:[],intent:{}}));
page.byId('routine-review').click(); await settle();
const body = JSON.parse(page.calls.find(call => call.url.endsWith('/review')).body);
assert.equal(body.schedule_id, 7);
assert.equal(body.definition.algorithm_name, 'BasicShuffle');
assert.equal(body.definition.algorithm_params.keep_first, 4);
assert.equal('timing' in body.definition, false);
assert.equal('desired_enabled' in body, false);
"""
    run_page(node, html, script)


def test_rotation_to_shuffle_sends_clean_algorithm_options(node, db_app):
    row = exact_row()
    row["definition"]["job_type"] = "rotate"
    row["definition"]["algorithm_name"] = None
    row["definition"]["algorithm_params"] = {"rotation_mode": "swap", "rotation_count": 8,
                                              "target_size": 50, "protect_count": 2}
    html = page_html(db_app, schedule_id=7)
    script = "const CONTEXT = " + json.dumps(context([row])) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new?schedule_id=7',
    route:() => page.reply(CONTEXT)});
await settle();
page.byId('routine-operation').value='shuffle'; page.change(page.byId('routine-operation'));
page.replies.push(page.reply({success:false,error_code:'invalid_definition',message:'Check options'},422));
page.byId('routine-review').click(); await settle();
const body = JSON.parse(page.calls.find(call => call.url.endsWith('/review')).body);
assert.equal(body.schedule_id,7);
assert.equal(body.definition.job_type,'shuffle');
assert.equal(body.definition.algorithm_name,'BasicShuffle');
assert.equal('rotation_count' in body.definition.algorithm_params,false);
assert.equal('target_size' in body.definition.algorithm_params,false);
assert.equal('protect_count' in body.definition.algorithm_params,false);
"""
    run_page(node, html, script)


def test_pause_is_available_even_when_collection_prerequisites_are_missing(node, db_app):
    row = exact_row()
    row["schedule"]["is_enabled"] = True
    row["state"] = "Active"
    row["definition"]["job_type"] = "raid"
    row["definition"]["algorithm_name"] = None
    row["definition"]["algorithm_params"] = {}
    row["registration"] = {"status": "registered", "next_run_at": "2026-10-14T00:00:00Z"}
    payload = context([row])
    payload["prerequisites"]["sources"] = []
    payload["capabilities"]["raid"] = {"available": False, "reason": "Save a source first."}
    html = page_html(db_app, schedule_id=7)
    script = "const CONTEXT = " + json.dumps(payload) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new?schedule_id=7',
    route:() => page.reply(CONTEXT)});
await settle();
assert.equal(page.byId('routine-state').hidden,false);
assert.match(page.text(page.byId('routine-operation-help')), /Save a source first/);
page.replies.push(page.reply({success:true,schedule_id:7,schedule_revision:REV,state:'Paused',
    schedule:{id:7,is_enabled:false},registration:{status:'paused',next_run_at:null}}));
page.byId('routine-state').click(); await settle();
const writes = page.calls.filter(call => call.method !== 'GET');
assert.equal(writes.length,1);
assert.equal(writes[0].url,'/routines/7/state');
assert.equal(JSON.parse(writes[0].body).desired_enabled,false);
assert.equal(page.byId('routine-state').textContent,'Resume routine #7');
"""
    run_page(node, html, script)


def test_review_discovered_candidates_return_to_exact_choice_without_losing_options(node, db_app):
    html = page_html(db_app)
    script = "const EMPTY = " + json.dumps(context()) + "; const WITH_ROW = " + json.dumps(
        context([exact_row()])) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(EMPTY)});
await settle();
page.$('[data-goal="all"]').click();
page.byId('routine-operation').value='shuffle'; page.change(page.byId('routine-operation'));
page.byId('routine-param-keep_first').value='2'; page.input(page.byId('routine-param-keep_first'));
page.byId('routine-next').click();
page.replies.push(page.reply({success:true, definition:{job_type:'shuffle',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:'BasicShuffle',
    algorithm_params:{keep_first:2},schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},
    resource_scope:{target_id:'target',source_ids:[4],routine_only_source_playlist_ids:[],
    collection_id:1,archive_id:2},dependency_revision:REV,schedule_revision:null,
    candidate_schedule_ids:[7],warnings:['Choose an exact routine'],intent:{signed:'intent'}}));
page.replies.push(page.reply(WITH_ROW));
page.byId('routine-review').click(); await settle();
assert.equal(page.byId('routine-connections-step').hidden,false);
assert.equal(page.byId('routine-activate').hidden,true);
assert.equal(page.byId('routine-candidates').hidden,false);
assert.equal(page.$('[data-action-click="routine.choose"][data-schedule-id="7"]') !== null,true);
assert.equal(page.byId('routine-param-keep_first').value,'2');
page.byId('routine-create-another').click();
assert.equal(page.byId('routine-param-keep_first').value,'2');
assert.equal(page.byId('routine-candidates').hidden,false);
"""
    run_page(node, html, script)


def test_existing_candidates_at_activation_refreshes_and_invalidates_review(node, db_app):
    html = page_html(db_app)
    script = "const EMPTY = " + json.dumps(context()) + "; const WITH_ROW = " + json.dumps(
        context([exact_row()])) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(EMPTY)});
await settle(); page.$('[data-goal="all"]').click();
page.byId('routine-operation').value='shuffle'; page.change(page.byId('routine-operation'));
page.byId('routine-next').click();
page.replies.push(page.reply({success:true,definition:{job_type:'shuffle',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:'BasicShuffle',
    algorithm_params:{keep_first:0},schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},
    resource_scope:{target_id:'target',source_ids:[4],routine_only_source_playlist_ids:[]},
    dependency_revision:REV,schedule_revision:null,candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.byId('routine-review').click(); await settle();
page.replies.push(page.reply({success:false,error_code:'existing_candidates',message:'Choose an exact routine.',
    candidate_schedule_ids:[7]},409));
page.replies.push(page.reply(WITH_ROW));
page.byId('routine-activate').click(); await settle();
assert.equal(page.byId('routine-connections-step').hidden,false);
assert.equal(page.byId('routine-activate').hidden,true);
assert.equal(page.byId('routine-candidates').hidden,false);
assert.match(page.text(page.byId('routine-status')), /exact ID|exact routine/i);
"""
    run_page(node, html, script)


def test_review_uses_resource_names_from_matching_fresh_context(node, db_app):
    initial = context()
    initial["prerequisites"]["sources"][0]["name"] = "Old source"
    initial["prerequisites"]["collection"]["name"] = "Old collection"
    initial["prerequisites"]["archive"]["name"] = "Old archive"
    fresh = context()
    fresh["dependency_revision"] = "b" * 64
    fresh["prerequisites"]["sources"][0]["name"] = "New source"
    fresh["prerequisites"]["collection"]["name"] = "New collection"
    fresh["prerequisites"]["archive"]["name"] = "New archive"
    script = "const INITIAL = " + json.dumps(initial) + "; const FRESH = " + json.dumps(fresh) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(INITIAL)});
await settle(); page.$('[data-goal="all"]').click(); page.byId('routine-next').click();
const revealed = [];
page.window.requestAnimationFrame = callback => setImmediate(callback);
page.byId('routine-review-heading').scrollIntoView = options => revealed.push(options);
page.replies.push(page.reply({success:true,definition:{job_type:'raid',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:null,algorithm_params:{},
    schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},resource_scope:{target_id:'target',
    source_ids:[4],routine_only_source_playlist_ids:[]},dependency_revision:FRESH.dependency_revision,
    schedule_revision:null,candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.replies.push(page.reply(FRESH));
page.byId('routine-review').click(); await settle();
assert.equal(page.byId('routine-review-step').hidden,false);
assert.equal(page.byId('routine-activate').hidden,false);
assert.equal(page.document.activeElement,page.byId('routine-review-heading'));
assert.equal(page.focusOptions.preventScroll,true);
assert.deepEqual(revealed.map(options => options.block),['start']);
const words = page.text(page.byId('routine-review-details'));
for (const name of ['New source','New collection','New archive']) assert.match(words,new RegExp(name));
for (const name of ['Old source','Old collection','Old archive']) assert.equal(words.includes(name),false);
assert.equal(page.calls.filter(call => call.url.endsWith('/context')).length,2);
"""
    run_page(node, page_html(db_app), script)


def test_review_invalidates_when_setup_changes_during_confirmation(node, db_app):
    changed = context()
    changed["dependency_revision"] = "c" * 64
    changed["prerequisites"]["sources"][0]["name"] = "Latest source"
    script = "const INITIAL = " + json.dumps(context()) + "; const CHANGED = " + json.dumps(
        changed) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(INITIAL)});
await settle(); page.$('[data-goal="all"]').click();
page.byId('routine-operation').value='shuffle'; page.change(page.byId('routine-operation'));
page.byId('routine-param-keep_first').value='2'; page.input(page.byId('routine-param-keep_first'));
page.byId('routine-next').click();
page.replies.push(page.reply({success:true,definition:{job_type:'shuffle',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:'BasicShuffle',
    algorithm_params:{keep_first:2},schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},
    resource_scope:{target_id:'target',source_ids:[4],routine_only_source_playlist_ids:[]},
    dependency_revision:'b'.repeat(64),schedule_revision:null,candidate_schedule_ids:[],
    warnings:[],intent:{signed:'intent'}}));
page.replies.push(page.reply(CHANGED));
page.byId('routine-review').click(); await settle();
assert.equal(page.byId('routine-review-step').hidden,true);
assert.equal(page.byId('routine-activate').hidden,true);
assert.equal(page.byId('routine-connections-step').hidden,false);
assert.equal(page.byId('routine-param-keep_first').value,'2');
assert.match(page.text(page.byId('routine-status')),/changed after Review/);
assert.equal(page.calls.filter(call => call.url.endsWith('/activate')).length,0);
"""
    run_page(node, page_html(db_app), script)


def test_timing_error_is_associated_and_review_is_friendly_and_relevant(node, db_app):
    html = page_html(db_app)
    script = "const CONTEXT = " + json.dumps(context()) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(CONTEXT)});
await settle(); page.$('[data-goal="all"]').click();
page.byId('routine-operation').value='shuffle'; page.change(page.byId('routine-operation'));
assert.match(page.byId('routine-algorithm').getAttribute('aria-describedby'), /routine-algorithm-error/);
assert.match(page.byId('routine-param-keep_first').getAttribute('aria-describedby'), /routine-param-keep_first-error/);
assert.match(page.byId('routine-target-size').getAttribute('aria-describedby'), /routine-target-size-error/);
page.byId('routine-next').click();
page.replies.push(page.reply({success:false,error_code:'invalid_definition',message:'Timing needs attention',
    field_errors:[{path:'definition.timing',message:'Choose a valid IANA timezone.'}]},422));
page.byId('routine-review').click(); await settle();
assert.equal(page.byId('routine-timing-error').hidden,false);
assert.equal(page.byId('routine-timing-error').textContent,'Choose a valid IANA timezone.');
assert.equal(page.byId('routine-timing-kind').getAttribute('aria-invalid'),'true');
assert.match(page.byId('routine-timing-kind').getAttribute('aria-describedby'), /routine-timing-error/);
page.replies.push(page.reply({success:true,definition:{job_type:'shuffle',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:'BasicShuffle',
    algorithm_params:{keep_first:2},schedule_type:'cron',schedule_value:'35 9 * * fri',
    timezone:'America/New_York'},resource_scope:{target_id:'target',source_ids:[4],
    routine_only_source_playlist_ids:[]},dependency_revision:REV,schedule_revision:null,
    candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.byId('routine-review').click(); await settle();
const words = page.text(page.byId('routine-review-details'));
assert.match(words,/Basic Shuffle/);
assert.match(words,/Keep first tracks/);
assert.match(words,/Every Friday at 09:35 America\\/New_York/);
for (const jargon of ['BasicShuffle','{"keep_first"','Cron 35 9','Routine-only sources','Collection','Drip','Archive'])
    assert.equal(words.includes(jargon),false);
assert.equal(page.byId('routine-task').dataset.view,'review');
"""
    run_page(node, html, script)


def test_deleted_receipt_is_a_distinct_accepted_outcome(node, db_app):
    html = page_html(db_app)
    script = "const CONTEXT = " + json.dumps(context()) + "; const REV = " + json.dumps(REV) + ";\n" + """
const page = loadPage(TREE, {path:'/playlist/target/routines/new', route:() => page.reply(CONTEXT)});
await settle(); page.$('[data-goal="all"]').click(); page.byId('routine-next').click();
page.replies.push(page.reply({success:true,definition:{job_type:'raid',target_playlist_id:'target',
    target_playlist_name:'Mix <script>',source_playlist_ids:[],algorithm_name:null,algorithm_params:{},
    schedule_type:'interval',schedule_value:'daily',timezone:'UTC'},resource_scope:{target_id:'target',
    source_ids:[4],routine_only_source_playlist_ids:[]},dependency_revision:REV,
    schedule_revision:null,candidate_schedule_ids:[],warnings:[],intent:{signed:'intent'}}));
page.byId('routine-review').click(); await settle();
page.replies.push(page.reply({success:true,accepted:true,replayed:true,schedule_id:9,
    accepted_at:'2026-10-13T00:00:00Z',dependency_revision:REV,state:'Deleted',schedule:null}));
page.byId('routine-activate').click(); await settle();
assert.equal(page.byId('routine-result-step').hidden,false);
assert.match(page.text(page.byId('routine-result-heading')), /later deleted/);
assert.equal(page.byId('routine-state').hidden,true);
assert.equal(page.byId('routine-result-actions').children.length,0);
"""
    run_page(node, html, script)
