import {createBridgeController} from './bridge-controller.js';
import {BridgeError, publicError} from './bridge-core.js';

const controller = createBridgeController(chrome);
// Never expose our local bridge token to content-script contexts. This
// extension deliberately has no content scripts or externally_connectable.
chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'}).catch(() => {});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender.id !== chrome.runtime.id || sender.url !== chrome.runtime.getURL('popup.html')) {
    sendResponse(publicError(new BridgeError('invalid_message')));
    return false;
  }
  controller.message(message).then(sendResponse, () => sendResponse(publicError(new BridgeError('network_error'))));
  return true;
});
let cookieListenerRegistered = false;
function registerCookieListener() {
  if (!cookieListenerRegistered && chrome.cookies?.onChanged) {
    chrome.cookies.onChanged.addListener(change => { controller.cookieChanged(change).catch(() => {}); });
    cookieListenerRegistered = true;
  }
}
registerCookieListener();
chrome.permissions.onAdded.addListener(registerCookieListener);
chrome.alarms.onAlarm.addListener(alarm => { controller.alarmFired(alarm).catch(() => {}); });
