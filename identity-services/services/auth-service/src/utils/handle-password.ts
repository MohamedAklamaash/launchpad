import bcrypt from "bcrypt"

export const hashPassword = async (password: string): Promise<string> => {
    const salt = 12;
    return bcrypt.hash(password, salt);
}

export const comparePassword = async (inputPassword: string, orgPasswordHash: string): Promise<boolean> => {
    return bcrypt.compare(inputPassword, orgPasswordHash)
}