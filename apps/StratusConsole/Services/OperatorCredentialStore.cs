using System.Runtime.InteropServices;
using Windows.Security.Credentials;

namespace StratusConsole.Services;

public sealed class OperatorCredentialStore
{
    private const string ResourceName = "STRATUS Operator API";
    private const string UserName = "operator";

    public string? LoadApiKey()
    {
        try
        {
            PasswordCredential credential = new PasswordVault().Retrieve(ResourceName, UserName);
            credential.RetrievePassword();
            return string.IsNullOrWhiteSpace(credential.Password) ? null : credential.Password;
        }
        catch (COMException)
        {
            return null;
        }
    }

    public void SaveApiKey(string? apiKey)
    {
        PasswordVault vault = new();
        try
        {
            PasswordCredential existing = vault.Retrieve(ResourceName, UserName);
            vault.Remove(existing);
        }
        catch (COMException)
        {
        }

        if (!string.IsNullOrWhiteSpace(apiKey))
        {
            vault.Add(new PasswordCredential(ResourceName, UserName, apiKey));
        }
    }
}
