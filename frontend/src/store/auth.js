import { reactive } from 'vue'

const TOKEN_KEY = 'miroshark_token'
const USER_KEY = 'miroshark_user'

const state = reactive({
  token: localStorage.getItem(TOKEN_KEY) || '',
  username: localStorage.getItem(USER_KEY) || ''
})

export function getToken() {
  return state.token
}

export function getUsername() {
  return state.username
}

export function isAuthenticated() {
  return !!state.token
}

export function setAuth(token, username) {
  state.token = token
  state.username = username
  localStorage.setItem(TOKEN_KEY, token)
  localStorage.setItem(USER_KEY, username)
}

export function clearAuth() {
  state.token = ''
  state.username = ''
  localStorage.removeItem(TOKEN_KEY)
  localStorage.removeItem(USER_KEY)
}
