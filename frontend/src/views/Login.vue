<template>
  <div class="login-container">
    <div class="login-card">
      <h1 class="login-title">MiroShark</h1>
      <p class="login-subtitle">Sign in to continue</p>

      <form @submit.prevent="handleLogin" class="login-form">
        <div class="form-group">
          <label for="username">Username</label>
          <input
            id="username"
            v-model="username"
            type="text"
            autocomplete="username"
            placeholder="Enter username"
            :disabled="loading"
            required
          />
        </div>

        <div class="form-group">
          <label for="password">Password</label>
          <input
            id="password"
            v-model="password"
            type="password"
            autocomplete="current-password"
            placeholder="Enter password"
            :disabled="loading"
            required
          />
        </div>

        <p v-if="error" class="error-message">{{ error }}</p>

        <button type="submit" class="login-button" :disabled="loading">
          {{ loading ? 'Signing in...' : 'Sign in' }}
        </button>
      </form>
    </div>
  </div>
</template>

<script setup>
import { ref } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { setAuth } from '../store/auth'
import service from '../api'

const router = useRouter()
const route = useRoute()
const username = ref('')
const password = ref('')
const error = ref('')
const loading = ref(false)

async function handleLogin() {
  error.value = ''
  loading.value = true

  try {
    const res = await service.post('/api/auth/login', {
      username: username.value,
      password: password.value
    })
    setAuth(res.token, res.username)

    const redirect = route.query.redirect || '/'
    router.replace(redirect)
  } catch (err) {
    error.value = err?.response?.data?.error || err.message || 'Login failed'
  } finally {
    loading.value = false
  }
}
</script>

<style scoped>
.login-container {
  display: flex;
  align-items: center;
  justify-content: center;
  min-height: 100vh;
  background: #ffffff;
}

.login-card {
  width: 100%;
  max-width: 380px;
  padding: 48px 32px;
}

.login-title {
  font-family: 'Space Grotesk', sans-serif;
  font-size: 32px;
  font-weight: 700;
  margin-bottom: 4px;
}

.login-subtitle {
  color: #666;
  margin-bottom: 32px;
  font-size: 14px;
}

.login-form {
  display: flex;
  flex-direction: column;
  gap: 20px;
}

.form-group {
  display: flex;
  flex-direction: column;
  gap: 6px;
}

.form-group label {
  font-size: 13px;
  font-weight: 500;
  color: #333;
}

.form-group input {
  padding: 10px 12px;
  border: 1px solid #ddd;
  border-radius: 6px;
  font-size: 14px;
  font-family: inherit;
  outline: none;
  transition: border-color 0.15s;
}

.form-group input:focus {
  border-color: #000;
}

.error-message {
  color: #d32f2f;
  font-size: 13px;
  margin: 0;
}

.login-button {
  padding: 10px 0;
  background: #000;
  color: #fff;
  border: none;
  border-radius: 6px;
  font-size: 14px;
  font-weight: 600;
  cursor: pointer;
  transition: background 0.15s;
}

.login-button:hover:not(:disabled) {
  background: #333;
}

.login-button:disabled {
  opacity: 0.6;
  cursor: not-allowed;
}
</style>
