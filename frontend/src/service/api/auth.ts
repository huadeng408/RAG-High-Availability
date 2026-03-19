import { request } from '../request';
import { localStg } from '@/utils/storage';

/**
 * Login
 *
 * @param username User name
 * @param password Password
 */
export function fetchLogin(username: string, password: string) {
  return request<Api.Auth.LoginToken>({
    url: '/users/login',
    method: 'post',
    data: {
      username,
      password
    }
  });
}

export function fetchLogout() {
  const refreshToken = localStg.get('refreshToken') || '';

  return request({
    url: '/users/logout',
    method: 'post',
    headers: refreshToken ? { 'X-Refresh-Token': refreshToken } : undefined
  });
}

export function fetchRegister(username: string, password: string) {
  return request({
    url: '/users/register',
    method: 'post',
    data: {
      username,
      password
    }
  });
}

/** Get user info */
export function fetchGetUserInfo() {
  return request<Api.Auth.UserInfo>({ url: '/users/me' });
}

/**
 * Refresh token
 *
 * @param refreshToken Refresh token
 */
export function fetchRefreshToken(refreshToken: string) {
  return request<Api.Auth.LoginToken>({
    url: '/auth/refreshToken',
    method: 'post',
    data: {
      refreshToken
    }
  });
}

/**
 * return custom backend error
 *
 * @param code error code
 * @param msg error message
 */
export function fetchCustomBackendError(code: string, msg: string) {
  return request({ url: '/auth/error', params: { code, msg } });
}
