(function() {
        var meta = document.querySelector('meta[name="csrf-token"]');
        if (!meta) return;
        var token = meta.getAttribute('content');
        var _fetch = window.fetch;
        window.fetch = function(url, opts) {
            opts = opts || {};
            var method = (opts.method || 'GET').toUpperCase();
            if (method !== 'GET' && method !== 'HEAD' && method !== 'OPTIONS') {
                if (opts.headers instanceof Headers) {
                    if (!opts.headers.has('X-CSRFToken')) {
                        opts.headers.set('X-CSRFToken', token);
                    }
                } else {
                    opts.headers = opts.headers || {};
                    var hasCsrfHeader = Object.keys(opts.headers).some(function(name) {
                        return name.toLowerCase() === 'x-csrftoken';
                    });
                    if (!hasCsrfHeader) {
                        opts.headers['X-CSRFToken'] = token;
                    }
                }
            }
            return _fetch.call(this, url, opts);
        };
    })();

// Native disclosure provides the no-JS menu; Escape restores its trigger.
document.addEventListener('DOMContentLoaded', function () {
    var menu = document.querySelector('.sh-app-menu');
    if (!menu) return;
    var summary = menu.querySelector('summary');
    summary.setAttribute('aria-expanded', String(menu.open));
    menu.addEventListener('toggle', function () {
        summary.setAttribute('aria-expanded', String(menu.open));
    });
    menu.addEventListener('keydown', function (event) {
        if (event.key !== 'Escape' || !menu.open) return;
        event.preventDefault();
        menu.open = false;
        summary.focus();
    });
    var breakpoint = getComputedStyle(document.documentElement)
        .getPropertyValue('--sh-breakpoint-workspace').trim();
    var wide = window.matchMedia('(min-width: ' + breakpoint + ')');
    wide.addEventListener('change', function () {
        var focused = document.activeElement;
        if (wide.matches && menu.contains(focused)) {
            var key = focused.dataset.navKey;
            var selector = key ? '[data-nav-key="' + key + '"]' : '.sh-brand';
            document.querySelector('.sh-app-nav-desktop ' + selector).focus();
        } else if (!wide.matches && focused.closest('.sh-app-nav-desktop')) {
            summary.focus();
        }
    });
});
