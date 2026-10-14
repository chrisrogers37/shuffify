/* Shared feedback for authenticated pages. Text is never HTML.
   options.spoken === false shows a toast whose words the page's announcer already said. */
(function () {
    'use strict';
    window.showNotification = function (message, type, options) {
        var container = document.getElementById('flash-messages');
        if (!container) return;
        var notice = document.createElement('div');
        notice.className = 'sh-toast fade-out';
        if (type === 'error' || type === 'success') {
            notice.classList.add('sh-toast-' + type);
        }
        if (options && options.spoken === false) notice.setAttribute('aria-hidden', 'true');
        else notice.setAttribute('role', type === 'error' ? 'alert' : 'status');
        notice.textContent = message;
        container.appendChild(notice);
        setTimeout(function () {
            notice.classList.add('hiding');
            setTimeout(function () { notice.remove(); }, 300);
        }, 5000);
        return notice;
    };
})();
