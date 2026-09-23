package com.phantasma.app.ui.theme

import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.ui.graphics.Color

// Light color scheme
private val LightColorScheme = lightColorScheme(
    primary = Color(0xFF22C55E),
    primaryContainer = Color(0xFFDCFCE7),
    onPrimary = Color.White,
    onPrimaryContainer = Color(0xFF14532E),
    secondary = Color(0xFF475569),
    secondaryContainer = Color(0xFFE2E8F0),
    onSecondary = Color.White,
    onSecondaryContainer = Color(0xFF1E293B),
    tertiary = Color(0xFFF59E0B),
    tertiaryContainer = Color(0xFFFEF3C7),
    onTertiary = Color.White,
    onTertiaryContainer = Color(0xFF78350F),
    error = Color(0xFFEF4444),
    errorContainer = Color(0xFFFEE2E2),
    onError = Color.White,
    onErrorContainer = Color(0xFF7F1D1D),
    background = Color(0xFFFAFAFA),
    onBackground = Color(0xFF171717),
    surface = Color.White,
    surfaceVariant = Color(0xFFF5F5F5),
    onSurface = Color(0xFF171717),
    onSurfaceVariant = Color(0xFF737373),
    outline = Color(0xFFE5E5E5),
    outlineVariant = Color(0xFFE5E5E5),
)

// Dark color scheme (matches pHantasma aesthetic)
private val DarkColorScheme = darkColorScheme(
    primary = Color(0xFF22C55E),
    primaryContainer = Color(0xFF166534),
    onPrimary = Color.White,
    onPrimaryContainer = Color(0xFFDCFCE7),
    secondary = Color(0xFF94A3B8),
    secondaryContainer = Color(0xFF334155),
    onSecondary = Color.White,
    onSecondaryContainer = Color(0xFFE2E8F0),
    tertiary = Color(0xFFF59E0B),
    tertiaryContainer = Color(0xFF854D0E),
    onTertiary = Color.White,
    onTertiaryContainer = Color(0xFFFEF3C7),
    error = Color(0xFFEF4444),
    errorContainer = Color(0xFF7F1D1D),
    onError = Color.White,
    onErrorContainer = Color(0xFFFEE2E2),
    background = Color(0xFF0A0A0A),
    onBackground = Color(0xFFFAFAFA),
    surface = Color(0xFF171717),
    surfaceVariant = Color(0xFF262626),
    onSurface = Color(0xFFFAFAFA),
    onSurfaceVariant = Color(0xFF737373),
    outline = Color(0xFF404040),
    outlineVariant = Color(0xFF525252),
)

@Composable
fun PhantasmaTheme(
    darkTheme: Boolean = true,
    content: @Composable () -> Unit
) {
    val colorScheme = if (darkTheme) DarkColorScheme else LightColorScheme
    MaterialTheme(
        colorScheme = colorScheme,
        typography = Typography,
        content = content
    )
}

// Preview
@Preview
@Composable
fun PhantasmaThemePreview() {
    PhantasmaTheme {
        androidx.compose.material3.Text("pHantasma Theme Preview")
    }
}