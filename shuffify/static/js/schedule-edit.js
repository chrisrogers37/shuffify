/**
 * Shared schedule editing utilities.
 *
 * Used by both the Workshop sidebar (workshop.html) and the
 * global Schedules page (schedules.html). Requires:
 *   - window.SCHED_ALGORITHMS  (array of {class_name, name})
 *   - window.SCHED_UPSTREAM_SOURCES  (map: playlistId -> [source...])
 *   - showNotification(message, type) from notifications.js
 *   - escapeHtml(str) defined on the page
 */

var ScheduleEdit = {

    parseCron: function(schedType, schedValue) {
        var result = { frequency: 'daily', time: '09:00' };
        if (schedType === 'cron' && schedValue) {
            var parts = schedValue.split(' ');
            if (parts.length === 5) {
                var min = parseInt(parts[0], 10);
                var hr = parseInt(parts[1], 10);
                result.time = ('0' + hr).slice(-2) + ':' + ('0' + min).slice(-2);
                if (parts[2] === '*/3') result.frequency = 'every_3d';
                else if (parts[4] === '1') result.frequency = 'weekly';
                else result.frequency = 'daily';
            }
        } else {
            result.frequency = schedValue || 'daily';
            if (schedValue === 'every_6h' || schedValue === 'every_12h') {
                result.time = '';
            }
        }
        return result;
    },

    getFreqText: function(sched) {
        if (sched.schedule_type === 'cron' && sched.schedule_value) {
            var parts = sched.schedule_value.split(' ');
            if (parts.length === 5) {
                var hr = parseInt(parts[1], 10);
                var min = parseInt(parts[0], 10);
                var timeStr = ('0' + hr).slice(-2) + ':' + ('0' + min).slice(-2) + ' UTC';
                if (parts[2] === '*/3') return 'Every 3 days at ' + timeStr;
                if (parts[4] === '1') return 'Weekly at ' + timeStr;
                return 'Daily at ' + timeStr;
            }
        }
        var map = {
            daily: 'Daily',
            every_3d: 'Every 3 days',
            weekly: 'Weekly',
            every_6h: 'Every 6h',
            every_12h: 'Every 12h',
        };
        return map[sched.schedule_value] || (sched.schedule_value || '').replace(/_/g, ' ');
    },

    buildScheduleValue: function(frequency, timeValue, hasTime) {
        var scheduleType = 'interval';
        var scheduleValue = frequency;
        if (hasTime && timeValue) {
            var timeParts = timeValue.split(':').map(Number);
            scheduleType = 'cron';
            if (frequency === 'daily') {
                scheduleValue = timeParts[1] + ' ' + timeParts[0] + ' * * *';
            } else if (frequency === 'every_3d') {
                scheduleValue = timeParts[1] + ' ' + timeParts[0] + ' */3 * *';
            } else if (frequency === 'weekly') {
                scheduleValue = timeParts[1] + ' ' + timeParts[0] + ' * * 1';
            }
        }
        return { schedule_type: scheduleType, schedule_value: scheduleValue };
    },

    /**
     * @param {Object} sched      - schedule data from the API
     * @param {string} playlistId - target playlist ID for source lookup; null for global page
     */
    buildEditForm: function(sched, playlistId) {
        var parsed = this.parseCron(sched.schedule_type, sched.schedule_value);
        var params = sched.algorithm_params || {};
        var jobType = sched.job_type || '';
        var isRaidType = (jobType === 'raid' || jobType === 'raid_and_shuffle' || jobType === 'raid_and_drip');
        var isShuffleType = (jobType === 'shuffle' || jobType === 'raid_and_shuffle');
        var isRotateType = (jobType === 'rotate');
        var showTime = (parsed.frequency === 'daily' || parsed.frequency === 'every_3d' || parsed.frequency === 'weekly');

        var html = '<div class="space-y-3">';

        html += '<div>' +
            '<label class="block text-xs font-medium text-white/70 mb-1">Frequency</label>' +
            '<select class="sched-edit-freq w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs">' +
                '<option value="every_6h"' + (parsed.frequency === 'every_6h' ? ' selected' : '') + '>Every 6 hours</option>' +
                '<option value="every_12h"' + (parsed.frequency === 'every_12h' ? ' selected' : '') + '>Every 12 hours</option>' +
                '<option value="daily"' + (parsed.frequency === 'daily' ? ' selected' : '') + '>Daily</option>' +
                '<option value="every_3d"' + (parsed.frequency === 'every_3d' ? ' selected' : '') + '>Every 3 days</option>' +
                '<option value="weekly"' + (parsed.frequency === 'weekly' ? ' selected' : '') + '>Weekly</option>' +
            '</select>' +
        '</div>';

        html += '<div class="sched-edit-time-section' + (showTime ? '' : ' hidden') + '">' +
            '<label class="block text-xs font-medium text-white/70 mb-1">Preferred Time (UTC)</label>' +
            '<input type="time" class="sched-edit-time w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs" value="' + (parsed.time || '09:00') + '">' +
        '</div>';

        if (isRaidType) {
            var lookupId = playlistId || sched.target_playlist_id;
            var sources = (window.SCHED_UPSTREAM_SOURCES || {})[lookupId] || [];
            var selectedIds = sched.source_playlist_ids || [];
            html += '<div>' +
                '<label class="block text-xs font-medium text-white/70 mb-1">Raid Sources</label>';
            if (sources.length === 0) {
                html += '<p class="text-amber-300/60 text-[10px]">No sources configured for this playlist.</p>';
            } else {
                html += '<div class="max-h-28 overflow-y-auto space-y-1 bg-white/5 rounded-lg p-1.5">';
                sources.forEach(function(src) {
                    var checked = selectedIds.indexOf(src.source_playlist_id) !== -1 ? ' checked' : '';
                    var srcName = src.source_playlist_name || src.source_playlist_id;
                    html += '<label class="flex items-center gap-2 px-1.5 py-1 rounded hover:bg-white/5 cursor-pointer">' +
                        '<input type="checkbox" class="sched-edit-source rounded border-white/30 bg-white/10 text-spotify-green focus:ring-spotify-green" value="' + escapeHtml(src.source_playlist_id) + '"' + checked + '>' +
                        '<span class="text-white/70 text-xs truncate">' + escapeHtml(srcName) + '</span>' +
                    '</label>';
                });
                html += '</div>';
            }
            html += '</div>';
        }

        if (isShuffleType) {
            var algos = window.SCHED_ALGORITHMS || [];
            html += '<div>' +
                '<label class="block text-xs font-medium text-white/70 mb-1">Algorithm</label>' +
                '<select class="sched-edit-algo w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs">';
            algos.forEach(function(algo) {
                var selected = (algo.class_name === sched.algorithm_name) ? ' selected' : '';
                html += '<option value="' + escapeHtml(algo.class_name) + '"' + selected + '>' + escapeHtml(algo.name) + '</option>';
            });
            html += '</select></div>';

            html += '<div>' +
                '<label class="block text-xs font-medium text-white/70 mb-1">Keep Top Songs</label>' +
                '<input type="number" class="sched-edit-keep-first w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs" value="' + (params.keep_first || 0) + '" min="0" max="500">' +
                '<p class="text-white/30 text-[10px] mt-0.5">Songs to keep fixed at the top (0 = shuffle all)</p>' +
            '</div>';
        }

        if (isRotateType) {
            html += '<div>' +
                '<label class="block text-xs font-medium text-white/70 mb-1">Tracks per Rotation</label>' +
                '<input type="number" class="sched-edit-rot-count w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs" value="' + (params.rotation_count || 5) + '" min="1" max="50">' +
            '</div>';
            html += '<div>' +
                '<label class="block text-xs font-medium text-white/70 mb-1">Playlist Size Cap</label>' +
                '<input type="number" class="sched-edit-target-size w-full px-2 py-1.5 bg-white/10 border border-white/20 rounded-lg text-white text-xs" value="' + (params.target_size || '') + '" min="1" max="10000" placeholder="e.g. 50">' +
            '</div>';
        }

        html += '<div class="flex gap-2 pt-1">' +
            '<button class="sched-cancel-btn flex-1 px-2 py-1.5 rounded-lg bg-white/10 hover:bg-white/20 text-white text-xs font-semibold transition duration-150 border border-white/20">Cancel</button>' +
            '<button class="sched-save-btn flex-1 px-2 py-1.5 rounded-lg bg-spotify-green hover:bg-green-400 text-spotify-dark text-xs font-bold transition duration-150">Save</button>' +
        '</div>';

        html += '</div>';
        return html;
    },

    wireFrequencyToggle: function(container) {
        var freqSelect = container.querySelector('.sched-edit-freq');
        if (freqSelect) {
            freqSelect.addEventListener('change', function() {
                var timeDiv = container.querySelector('.sched-edit-time-section');
                if (timeDiv) {
                    var freq = this.value;
                    if (freq === 'daily' || freq === 'every_3d' || freq === 'weekly') {
                        timeDiv.classList.remove('hidden');
                    } else {
                        timeDiv.classList.add('hidden');
                    }
                }
            });
        }
    },

    /**
     * Read form inputs within `panel` and return the body object for PUT,
     * or null if validation fails (notification shown).
     */
    collectPayload: function(panel, jobType) {
        var freqSelect = panel.querySelector('.sched-edit-freq');
        var timeInput = panel.querySelector('.sched-edit-time');
        var frequency = freqSelect.value;
        var timeSection = panel.querySelector('.sched-edit-time-section');
        var hasTime = timeSection && !timeSection.classList.contains('hidden') && timeInput && timeInput.value;

        var sv = this.buildScheduleValue(frequency, timeInput ? timeInput.value : '', hasTime);
        var body = {
            schedule_type: sv.schedule_type,
            schedule_value: sv.schedule_value,
        };

        var isRaidType = (jobType === 'raid' || jobType === 'raid_and_shuffle' || jobType === 'raid_and_drip');
        if (isRaidType) {
            var checked = panel.querySelectorAll('.sched-edit-source:checked');
            body.source_playlist_ids = Array.from(checked).map(function(cb) { return cb.value; });
            if (body.source_playlist_ids.length === 0) {
                showNotification('Select at least one raid source.', 'error');
                return null;
            }
        }

        var isShuffleType = (jobType === 'shuffle' || jobType === 'raid_and_shuffle');
        if (isShuffleType) {
            var algoSelect = panel.querySelector('.sched-edit-algo');
            if (algoSelect) body.algorithm_name = algoSelect.value;
            var keepFirstInput = panel.querySelector('.sched-edit-keep-first');
            var keepFirst = keepFirstInput ? (parseInt(keepFirstInput.value, 10) || 0) : 0;
            body.algorithm_params = {};
            if (keepFirst > 0) body.algorithm_params.keep_first = keepFirst;
        }

        if (jobType === 'rotate') {
            var rotCount = panel.querySelector('.sched-edit-rot-count');
            var rotSize = panel.querySelector('.sched-edit-target-size');
            var targetSize = rotSize ? parseInt(rotSize.value, 10) : 0;
            if (!targetSize || targetSize < 1) {
                showNotification('Please enter a playlist size cap.', 'error');
                return null;
            }
            body.algorithm_params = {
                rotation_mode: 'swap',
                rotation_count: rotCount ? (parseInt(rotCount.value, 10) || 5) : 5,
                target_size: targetSize,
            };
        }

        return body;
    },

    saveSchedule: function(schedId, panel, jobType, onSuccess) {
        var body = this.collectPayload(panel, jobType);
        if (!body) return;

        var saveBtn = panel.querySelector('.sched-save-btn');
        if (saveBtn) { saveBtn.disabled = true; saveBtn.textContent = 'Saving...'; }

        fetch('/schedules/' + schedId, {
            method: 'PUT',
            headers: {
                'Content-Type': 'application/json',
                'X-Requested-With': 'XMLHttpRequest',
            },
            body: JSON.stringify(body),
        })
        .then(function(r) {
            if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Update failed'); });
            return r.json();
        })
        .then(function(data) {
            if (data.success) {
                showNotification('Schedule updated', 'success');
                if (onSuccess) onSuccess(data);
            }
        })
        .catch(function(err) {
            showNotification(err.message, 'error');
            if (saveBtn) { saveBtn.disabled = false; saveBtn.textContent = 'Save'; }
        });
    },

    deleteSchedule: function(schedId, onSuccess) {
        if (!confirm('Delete this schedule? This cannot be undone.')) return;

        fetch('/schedules/' + schedId, {
            method: 'DELETE',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
        .then(function(r) {
            if (!r.ok) return r.json().then(function(d) { throw new Error(d.message || 'Delete failed'); });
            return r.json();
        })
        .then(function(data) {
            if (data.success) {
                showNotification('Schedule deleted', 'success');
                if (onSuccess) onSuccess(data);
            }
        })
        .catch(function(err) { showNotification(err.message, 'error'); });
    },

    toggleSchedule: function(schedId, onSuccess) {
        fetch('/schedules/' + schedId + '/toggle', {
            method: 'POST',
            headers: { 'X-Requested-With': 'XMLHttpRequest' },
        })
        .then(function(r) { if (!r.ok) throw new Error('Toggle failed'); return r.json(); })
        .then(function(data) {
            if (data.success) {
                showNotification('Schedule ' + (data.is_active ? 'resumed' : 'paused'), 'success');
                if (onSuccess) onSuccess(data);
            }
        })
        .catch(function(err) { showNotification(err.message, 'error'); });
    },
};
