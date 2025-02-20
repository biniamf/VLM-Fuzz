#!/bin/bash

adb root

adb install test.apk
adb install -t dumper.apk

adb shell pm grant io.uihierarchydumper android.permission.READ_EXTERNAL_STORAGE
adb shell pm grant io.uihierarchydumper android.permission.WRITE_EXTERNAL_STORAGE

while :
do
    adb shell am instrument -w -e class io.uihierarchydumper.UiHierarchyServerTest io.uihierarchydumper.test/androidx.test.runner.AndroidJUnitRunner
done