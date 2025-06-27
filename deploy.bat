rem Remove the dist folder if it exists
if exist dist rmdir /s /q dist
echo Removed dist folder.

rem Build the package
echo Building package...
python -m build

rem Upload the package
echo Uploading package...
python -m twine upload dist/*

echo Build and upload process finished.
