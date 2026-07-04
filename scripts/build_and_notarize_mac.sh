#!/usr/bin/env bash
# Build, firma (Developer ID), notarizza e impacchetta OCR Lab.app in un .dmg
# distribuibile su Apple Silicon senza l'errore "app danneggiata" di Gatekeeper.
#
# Prerequisito una tantum (richiede il tuo Apple ID, non eseguibile da qui):
#   xcrun notarytool store-credentials "OCRLab" \
#     --apple-id "<tua-email-apple-id>" \
#     --team-id "6X9CNCZ6MB" \
#     --password "<app-specific-password creata su appleid.apple.com>"
#
# Uso: scripts/build_and_notarize_mac.sh
set -euo pipefail
cd "$(dirname "$0")/.."

SIGN_ID="Developer ID Application: Luca Casarotti (6X9CNCZ6MB)"
NOTARY_PROFILE="OCRLab"
ENTITLEMENTS="scripts/entitlements.plist"
VERSION="$(.venv/bin/python -c 'from app.version import __version__; print(__version__)')"
APP="dist/OCR Lab.app"
STAGE="dist/dmg-stage"
DMG="dist/OCRLab-v${VERSION}-macos.dmg"
NOTARIZE_ZIP="dist/OCRLab-notarize.zip"

echo "==> Verifico credenziali notarytool (profilo: $NOTARY_PROFILE)"
xcrun notarytool history --keychain-profile "$NOTARY_PROFILE" >/dev/null

echo "==> Build PyInstaller"
.venv/bin/pyinstaller -y ocr_accessible_mac.spec

echo "==> Firma con Developer ID + hardened runtime (deep)"
codesign --deep --force --timestamp --options runtime \
  --entitlements "$ENTITLEMENTS" --sign "$SIGN_ID" "$APP"

echo "==> Verifica firma"
codesign --verify --deep --strict --verbose=2 "$APP"

echo "==> Notarizzo l'app (submit + wait, può richiedere alcuni minuti)"
rm -f "$NOTARIZE_ZIP"
ditto -c -k --keepParent "$APP" "$NOTARIZE_ZIP"
xcrun notarytool submit "$NOTARIZE_ZIP" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Staple ticket di notarizzazione sull'app"
xcrun stapler staple "$APP"

echo "==> Verifica Gatekeeper sull'app"
spctl -a -vv --type execute "$APP"

echo "==> Creo il .dmg"
rm -rf "$STAGE" "$DMG"
mkdir -p "$STAGE"
cp -R "$APP" "$STAGE/"
ln -s /Applications "$STAGE/Applications"
hdiutil create -volname "OCR Lab" -srcfolder "$STAGE" -ov -format UDZO "$DMG"

echo "==> Firmo il .dmg"
codesign --force --timestamp --sign "$SIGN_ID" "$DMG"

echo "==> Notarizzo il .dmg"
xcrun notarytool submit "$DMG" --keychain-profile "$NOTARY_PROFILE" --wait

echo "==> Staple ticket sul .dmg"
xcrun stapler staple "$DMG"

echo "==> Verifica finale Gatekeeper sul .dmg"
spctl -a -vv --type open --context context:primary-signature "$DMG"

rm -rf "$STAGE" "$NOTARIZE_ZIP"
echo "==> Fatto: $DMG"
