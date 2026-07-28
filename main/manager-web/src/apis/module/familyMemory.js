import { getServiceUrl } from '../api'
import RequestService from '../httpRequest'

function request(path, method, data, callback, failCallback) {
  RequestService.sendRequest()
    .url(`${getServiceUrl()}/admin/family-memory${path}`)
    .method(method)
    .data(data || {})
    .success((res) => {
      RequestService.clearRequestTime()
      callback && callback(res)
    })
    .fail((err) => {
      RequestService.clearRequestTime()
      failCallback && failCallback(err)
    })
    .send()
}

function query(values) {
  return new URLSearchParams(
    Object.entries(values).filter(([, value]) => value !== undefined && value !== null && value !== '')
  ).toString()
}

export default {
  getSettings(callback, failCallback) {
    request('/settings', 'GET', null, callback, failCallback)
  },
  saveSettings(data, callback, failCallback) {
    request('/settings', 'PUT', data, callback, failCallback)
  },
  preflight(data, callback, failCallback) {
    request('/preflight', 'POST', data, callback, failCallback)
  },
  listPersons(targetWs, callback, failCallback) {
    request(`/persons?${query({ targetWs })}`, 'GET', null, callback, failCallback)
  },
  getPerson(targetWs, personId, callback, failCallback) {
    request(`/persons/${encodeURIComponent(personId)}?${query({ targetWs })}`, 'GET', null, callback, failCallback)
  },
  createPerson(data, callback, failCallback) {
    request('/persons', 'POST', data, callback, failCallback)
  },
  renamePerson(personId, data, callback, failCallback) {
    request(`/persons/${encodeURIComponent(personId)}/name`, 'PUT', data, callback, failCallback)
  },
  setPersonEnabled(personId, data, callback, failCallback) {
    request(`/persons/${encodeURIComponent(personId)}/enabled`, 'PUT', data, callback, failCallback)
  },
  listVoiceprints(targetWs, personId, callback, failCallback) {
    request(`/voiceprints?${query({ targetWs, personId })}`, 'GET', null, callback, failCallback)
  },
  bindVoiceprint(data, callback, failCallback) {
    request('/voiceprints/bind', 'POST', data, callback, failCallback)
  },
  revokeVoiceprint(data, callback, failCallback) {
    request('/voiceprints/revoke', 'POST', data, callback, failCallback)
  },
  replaceVoiceprint(data, callback, failCallback) {
    request('/voiceprints/replace', 'POST', data, callback, failCallback)
  }
}
