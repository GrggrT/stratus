using Microsoft.UI.Xaml.Data;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace StratusConsole.Converters;

/// <summary>
/// Maps a MonitoringListItem accent/confidence marker to the badge background brush.
/// Null keeps the neutral theme fill.
/// </summary>
public sealed partial class AccentToBrushConverter : IValueConverter
{
    private static readonly SolidColorBrush Positive =
        new(Color.FromArgb(0xFF, 0x31, 0xC4, 0x8D));
    private static readonly SolidColorBrush Negative =
        new(Color.FromArgb(0xFF, 0xF8, 0x51, 0x49));
    private static readonly SolidColorBrush Warning =
        new(Color.FromArgb(0xFF, 0xFF, 0xB0, 0x2E));
    private static readonly SolidColorBrush Blue =
        new(Color.FromArgb(0xFF, 0x4C, 0xB3, 0xFF));
    private static readonly SolidColorBrush Violet =
        new(Color.FromArgb(0xFF, 0xB2, 0x7C, 0xFF));
    private static readonly SolidColorBrush Neutral =
        new(Color.FromArgb(0x33, 0x80, 0x80, 0x80));

    public object Convert(object value, Type targetType, object parameter, string language) =>
        (value as string) switch
        {
            "positive" or "Passed" or "High" or "A" => Positive,
            "negative" or "Blocked" or "Low" => Negative,
            "warning" or "Pending" => Warning,
            "B" => Blue,
            "C" => Violet,
            _ => Neutral,
        };

    public object ConvertBack(object value, Type targetType, object parameter, string language) =>
        throw new NotSupportedException();
}
