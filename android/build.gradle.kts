buildscript {
    dependencies {
        val androidGradlePlugin by libs.versions.androidGradlePlugin
        val kotlin by libs.versions.kotlin
        val composeCompiler by libs.versions.composeCompiler
        val hilt by libs.versions.hilt
        classpath("com.android.tools.build:gradle:$androidGradlePlugin")
        classpath("org.jetbrains.kotlin:kotlin-gradle-plugin:$kotlin")
        classpath("org.jetbrains.kotlin:kotlin-serialization:$kotlin")
        classpath("com.google.dagger:hilt-android-gradle-plugin:$hilt")
    }
}

allprojects {
    repositories {
        google()
        mavenCentral()
    }
}

tasks.register("clean", Delete::class) {
    delete(rootProject.buildDir)
}