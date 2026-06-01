import axios from 'axios'

const api = axios.create({
  baseURL: 'https://greenlamp-publisher-production-75fd.up.railway.app',
})

// Attach Supabase JWT to every request
api.interceptors.request.use(async (config) => {
  const { supabase } = await import('./supabase')
  const { data: { session } } = await supabase.auth.getSession()
  if (session?.access_token) {
    config.headers.Authorization = `Bearer ${session.access_token}`
  }
  return config
})

export default api
