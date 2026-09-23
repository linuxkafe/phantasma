# ProGuard rules for Phantasma Android App

# Keep Hilt generated classes
-keep class dagger.hilt.** { *; }
-keep class * extends dagger.hilt.android.HiltAndroidApp
-keep class * extends dagger.hilt.android.HiltViewModel
-keep class * extends dagger.hilt.android.HiltActivity
-keep class * extends dagger.hilt.android.HiltFragment
-keep class * extends dagger.hilt.android.HiltService
-keep class * extends dagger.hilt.android.HiltBroadcastReceiver

# Keep Kotlin serialization
-keep class kotlinx.serialization.** { *; }
-keep class * implements kotlinx.serialization.KSerializer
-keep class * implements kotlinx.serialization.descriptors.SerialDescriptor
-keep class kotlinx.serialization.** { *; }

# Keep Ktor client
-keep class io.ktor.** { *; }

# Keep Room
-keep class androidx.room.** { *; }
-keep class * extends androidx.room.RoomDatabase
-keep class * extends androidx.room.Entity
-keep class * extends androidx.room.Dao

# Keep Compose
-keep class androidx.compose.** { *; }

# Keep Material 3
-keep class androidx.compose.material3.** { *; }

# Keep Coroutines
-keep class kotlinx.coroutines.** { *; }

# Keep OkHttp
-keep class okhttp3.** { *; }
-keep class okio.** { *; }

# Keep AndroidX
-keep class androidx.** { *; }

# Keep app classes
-keep class com.phantasma.app.** { *; }

# Keep JSON serialization
-keep class kotlinx.serialization.json.** { *; }

# Keep reflection for Hilt
-keepattributes *Annotation*,RuntimeVisibleAnnotations,RuntimeInvisibleAnnotations
-keepattributes Signature
-keepattributes EnclosingMethod
-keepattributes InnerClasses

# Suppress warnings for missing classes
-dontwarn kotlinx.coroutines.**
-dontwarn kotlinx.serialization.**
-dontwarn io.ktor.**
-dontwarn okhttp3.**
-dontwarn okio.**