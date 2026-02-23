/**
 * Shared notification toast function.
 * Displays a temporary notification at the bottom-right of the screen.
 *
 * @param {string} message - The message text to display.
 * @param {string} type    - One of 'success', 'info', or 'error'.
 */
function showNotification(message, type) {
    const notification = document.createElement('div');
    const bgColor = type === 'success' ? 'bg-green-500/90' :
                    type === 'info' ? 'bg-blue-500/90' : 'bg-red-500/90';
    notification.className = `fixed bottom-4 right-4 px-6 py-3 rounded-lg shadow-lg backdrop-blur-md ${bgColor} text-white font-semibold transform transition duration-300 translate-y-16 opacity-0 z-50`;
    notification.textContent = message;
    document.body.appendChild(notification);

    setTimeout(() => {
        notification.classList.remove('translate-y-16', 'opacity-0');
    }, 100);

    setTimeout(() => {
        notification.classList.add('translate-y-16', 'opacity-0');
        setTimeout(() => notification.remove(), 300);
    }, 3000);
}
