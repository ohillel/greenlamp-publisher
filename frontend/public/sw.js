// Activate immediately — don't wait for existing tabs to close.
// This ensures the new SW takes over right away after an update.
self.addEventListener('install',  ()    => self.skipWaiting())
self.addEventListener('activate', event => event.waitUntil(clients.claim()))

self.addEventListener('push', event => {
  const data = event.data?.json() ?? {}
  event.waitUntil(
    self.registration.showNotification(data.title || 'Greenlamp Publisher', {
      body: data.body || '',
      icon: '/favicon.svg',
      requireInteraction: true,
    })
  )
})

self.addEventListener('notificationclick', event => {
  event.notification.close()
  event.waitUntil(clients.openWindow('/'))
})
